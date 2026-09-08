"""Read-only FreeTour back-office integration.

FreeTour exposes server-rendered booking pages rather than a documented API.
This adapter uses only the two GET requests used by its own bookings calendar.
"""
import base64
import json
import re
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .credentials import (clear_freetour_cancel, freetour_cancel_requested,
                          freetour_sync_lock, read_freetour_cookies,
                          save_freetour_cookies, write_freetour_sync_progress)
from .guruwalk import (AuthenticationError, IntegrationError, apply_snapshot,
                       SyncCancelled, identity)
from .models import Connection, VendorTour


class FreeTourAuthenticationError(AuthenticationError):
    pass


def _cloudflare_email(encoded):
    try:
        raw = bytes.fromhex(encoded)
        if len(raw) < 2:
            raise ValueError()
        return bytes(value ^ raw[0] for value in raw[1:]).decode("utf-8").lower()
    except (TypeError, ValueError, UnicodeError):
        raise IntegrationError("FreeTour returned an invalid protected email address.") from None


class _BookingsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.events = []
        self.event = None
        self.booking = None
        self.page_title = []
        self.page_date = ""

    @staticmethod
    def _attrs(attrs):
        return {name: value or "" for name, value in attrs}

    def handle_starttag(self, tag, attrs):
        attrs = self._attrs(attrs)
        classes = set(attrs.get("class", "").split())
        node = {"tag": tag, "classes": classes, "event": False, "booking": False}
        if tag == "div" and "booking-tourcard" in classes:
            if self.event is not None:
                raise IntegrationError("FreeTour returned nested departure cards.")
            tour_value = attrs.get("data-id", "")
            self.event = {
                "tour_id": tour_value[5:] if tour_value.startswith("tour-") else "",
                "event_id": "",
                "title": [],
                "time": [],
                "attendees": [],
                "bookings": [],
            }
            node["event"] = True
        if tag == "div" and "booking-person" in classes:
            if self.event is None or self.booking is not None:
                raise IntegrationError("FreeTour returned an invalid booking card structure.")
            status_classes = sorted(value for value in classes if value.startswith("booking-person--"))
            status = "cancelled" if "booking-person--cancelled" in status_classes else "confirmed"
            unknown = [value for value in status_classes if value not in {"booking-person--", "booking-person--cancelled"}]
            self.booking = {
                "id": attrs.get("data-booking-id", ""),
                "name": [], "adults": [], "children": [], "phone": [], "reference": [],
                "email": "", "status": status, "unknown_status_classes": unknown,
            }
            node["booking"] = True
        if self.event is not None and tag == "input" and attrs.get("name") == "id" and not self.event["event_id"]:
            self.event["event_id"] = attrs.get("value", "")
        if tag == "input" and attrs.get("id") == "dater":
            self.page_date = attrs.get("value", "")
        if self.booking is not None and tag == "a" and attrs.get("data-cfemail"):
            self.booking["email"] = _cloudflare_email(attrs["data-cfemail"])
        self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def _inside(self, class_name):
        return any(class_name in node["classes"] for node in self.stack)

    def handle_data(self, data):
        if self._inside("booking-title"):
            self.page_title.append(data)
        if self.event is not None:
            if self._inside("booking-tourcard__title"):
                self.event["title"].append(data)
            if self._inside("booking-tourcard__time"):
                self.event["time"].append(data)
            if self._inside("booking-tourcard__limit-value"):
                self.event["attendees"].append(data)
        if self.booking is not None:
            if self._inside("booking-person__name"):
                self.booking["name"].append(data)
            if self._inside("adults"):
                self.booking["adults"].append(data)
            if self._inside("children"):
                self.booking["children"].append(data)
            if self._inside("details-block__phone"):
                self.booking["phone"].append(data)
            if self._inside("details-block__ref"):
                self.booking["reference"].append(data)

    def handle_endtag(self, tag):
        if not self.stack:
            return
        index = len(self.stack) - 1
        while index >= 0 and self.stack[index]["tag"] != tag:
            index -= 1
        if index < 0:
            return
        closing = self.stack[index:]
        self.stack = self.stack[:index]
        for node in reversed(closing):
            if node["booking"]:
                self.event["bookings"].append(self.booking)
                self.booking = None
            if node["event"]:
                self.events.append(self.event)
                self.event = None


def _text(parts):
    return " ".join("".join(parts).split())


def parse_booking_page(body, expected_date):
    try:
        text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
    except UnicodeError:
        raise IntegrationError("FreeTour returned an unreadable booking page.") from None
    if "Just a moment" in text or "challenge-platform" in text:
        raise IntegrationError("FreeTour requested a browser security check. Sync paused; try again later.", "rate_limited")
    if "login" in text.lower() and "booking-title" not in text:
        raise FreeTourAuthenticationError("FreeTour access expired. Reconnect FreeTour.", "authentication")
    parser = _BookingsParser()
    try:
        parser.feed(text)
        parser.close()
    except IntegrationError:
        raise
    except Exception:
        raise IntegrationError("FreeTour's booking page format changed. Sync paused.") from None
    title = _text(parser.page_title)
    expected = expected_date.isoformat()
    if parser.page_date != expected or title != f"Bookings for {expected}":
        raise IntegrationError("FreeTour returned bookings for the wrong date. Sync paused.")

    events = []
    seen_events = set()
    seen_bookings = set()
    for raw_event in parser.events:
        event_id = str(raw_event["event_id"])
        tour_id = str(raw_event["tour_id"])
        title = _text(raw_event["title"])
        time_text = _text(raw_event["time"])
        attendees_text = _text(raw_event["attendees"])
        if not event_id or not tour_id or not title or event_id in seen_events:
            raise IntegrationError("FreeTour returned an invalid or duplicate departure. Sync paused.")
        seen_events.add(event_id)
        try:
            start_time = datetime.strptime(time_text, "%I:%M %p").strftime("%H:%M")
            attendee_match = re.match(r"^(\d+)\s*/", attendees_text)
            participant_count = int(attendee_match.group(1))
        except (AttributeError, TypeError, ValueError):
            raise IntegrationError("FreeTour returned an invalid departure time or count.") from None
        bookings = []
        for raw_booking in raw_event["bookings"]:
            booking_id = str(raw_booking["id"])
            if not booking_id or booking_id in seen_bookings or raw_booking["unknown_status_classes"]:
                raise IntegrationError("FreeTour returned an invalid booking status or duplicate ID. Sync paused.")
            seen_bookings.add(booking_id)
            try:
                adults = int(_text(raw_booking["adults"]))
                children = int(_text(raw_booking["children"]))
                if adults < 0 or children < 0:
                    raise ValueError()
            except (TypeError, ValueError):
                raise IntegrationError("FreeTour returned an invalid party size. Sync paused.") from None
            reference = _text(raw_booking["reference"])
            reference = re.sub(r"^Ref:\s*", "", reference)
            name = _text(raw_booking["name"])
            if not name:
                raise IntegrationError("FreeTour returned a booking without a guest name.")
            bookings.append({
                "id": booking_id,
                "name": name,
                "username": "booking:" + booking_id,
                "email": raw_booking["email"],
                "phone": _text(raw_booking["phone"]),
                "phonePrefix": "",
                "adults": adults,
                "children": children,
                "status": raw_booking["status"],
                "checkedIn": False,
                "reference": reference,
            })
        events.append(({
            "id": event_id,
            "tourId": tour_id,
            "title": title,
            "date": expected,
            "startTime": start_time,
            "participantCount": participant_count,
        }, bookings))
    return events


class FreeTourClient:
    def __init__(self, cookies=None, request_timeout=None, persist_cookies=True):
        self.cookies = dict(cookies or read_freetour_cookies())
        self.request_timeout = float(request_timeout or getattr(settings, "FREETOUR_REQUEST_TIMEOUT", 30))
        self.persist_cookies = persist_cookies
        self.request_count = 0

    def _request(self, path):
        self.request_count += 1
        deadline = time.monotonic() + self.request_timeout
        command = json.dumps({
            "path": path,
            "cookies": self.cookies,
            "timeout": self.request_timeout,
        }).encode()
        try:
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("freetour_transport.py"))],
                input=command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=max(0.001, deadline - time.monotonic()),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise IntegrationError("A FreeTour request timed out. Existing bookings were kept.", "timeout") from None
        if completed.returncode or len(completed.stdout) > 7_000_000:
            raise IntegrationError("FreeTour returned an unexpected response. Existing bookings were kept.")
        try:
            result = json.loads(completed.stdout)
        except (TypeError, ValueError, UnicodeError):
            raise IntegrationError("FreeTour returned an unexpected response. Existing bookings were kept.") from None
        if not result.get("ok"):
            status = result.get("status")
            if status in (301, 302, 303, 307, 308, 401, 403):
                raise FreeTourAuthenticationError("FreeTour access expired. Reconnect FreeTour.", "authentication")
            if status == 429:
                raise IntegrationError("FreeTour is rate limiting requests. Existing bookings were kept.", "rate_limited")
            if result.get("error") == "network":
                raise IntegrationError("Could not reach FreeTour. Existing bookings were kept.", "network")
            raise IntegrationError("FreeTour returned an unexpected response. Existing bookings were kept.")
        set_cookies = result.get("set_cookies") if isinstance(result.get("set_cookies"), list) else []
        for header in set_cookies:
            parsed = SimpleCookie()
            try:
                parsed.load(header)
            except CookieError:
                continue
            for name, morsel in parsed.items():
                if re.fullmatch(r"[A-Za-z0-9_-]+", name) and morsel.value:
                    self.cookies[name] = morsel.value
        if self.persist_cookies:
            save_freetour_cookies(self.cookies)
        try:
            return base64.b64decode(result["body"], validate=True), str(result.get("content_type") or "")
        except (KeyError, TypeError, ValueError):
            raise IntegrationError("FreeTour returned an unexpected response. Existing bookings were kept.") from None

    def month(self, year, month):
        body, content_type = self._request(f"/backoffice/get_booking/{year}/{month}?page=bookings")
        if "json" not in content_type.lower():
            if b"Just a moment" in body:
                raise IntegrationError("FreeTour requested a browser security check. Try again later.", "rate_limited")
            raise FreeTourAuthenticationError("FreeTour access expired. Reconnect FreeTour.", "authentication")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise IntegrationError("FreeTour returned an invalid calendar index.") from None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise IntegrationError("FreeTour returned an invalid calendar index.")
        result = {}
        for value, count in data.items():
            try:
                parsed = date.fromisoformat(value)
            except (TypeError, ValueError):
                raise IntegrationError("FreeTour returned an invalid calendar date.") from None
            if parsed.year != year or parsed.month != month or isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise IntegrationError("FreeTour returned an invalid calendar count.")
            result[parsed] = count
        return result

    def day(self, value):
        body, content_type = self._request(f"/backoffice/bookings?date={value.isoformat()}")
        if "html" not in content_type.lower():
            raise IntegrationError("FreeTour returned an unexpected booking page.")
        return parse_booking_page(body, value)

    def check_auth(self):
        today = timezone.localdate()
        self.month(today.year, today.month)


def sync_bounds(window="full"):
    today = timezone.localdate()
    days = getattr(settings, "FREETOUR_SYNC_DAYS", 30)
    if window == "today":
        return today, today
    if window == "tomorrow":
        return today + timedelta(days=1), today + timedelta(days=1)
    if window == "future":
        return today + timedelta(days=2), today + timedelta(days=days)
    if window == "full":
        return today - timedelta(days=1), today + timedelta(days=days)
    raise IntegrationError("Unknown FreeTour sync window.")


def _months(first, last):
    current = first.replace(day=1)
    while current <= last:
        yield current.year, current.month
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)


def fetch_snapshot(client, window="full", progress=None, should_cancel=None, run_id=None):
    progress = progress or (lambda phase, current=0, total=0, message="": None)
    should_cancel = should_cancel or (lambda _run_id=None: False)

    def is_cancelled():
        try:
            return bool(should_cancel(run_id))
        except TypeError:
            return bool(should_cancel())

    first, last = sync_bounds(window)
    if first > last:
        return []
    months = list(_months(first, last))
    indexed = {}
    progress("fetching", 0, len(months), "Loading FreeTour calendar…")
    for current, (year, month) in enumerate(months, start=1):
        if is_cancelled():
            raise SyncCancelled("Sync canceled. Existing data was kept.")
        indexed.update(client.month(year, month))
        progress("fetching", current, len(months),
                 f"Loaded FreeTour calendar ({current} of {len(months)})…")
    dates = sorted(value for value, count in indexed.items() if first <= value <= last and count > 0)
    total_steps = len(months) + len(dates)
    progress("fetching", len(months), total_steps,
             f"Loading booking days (0 of {len(dates)})…")
    snapshot = []
    seen_events = set()
    seen_bookings = set()
    for current, value in enumerate(dates, start=1):
        if is_cancelled():
            raise SyncCancelled("Sync canceled. Existing data was kept.")
        daily = client.day(value)
        actual_count = sum(len(bookings) for _, bookings in daily)
        if actual_count != indexed[value]:
            raise IntegrationError("FreeTour returned an incomplete booking page. Existing bookings were kept.")
        for event, bookings in daily:
            event_id = identity(event, "id")
            if event_id in seen_events:
                raise IntegrationError("FreeTour returned the same departure more than once.")
            seen_events.add(event_id)
            for booking in bookings:
                booking_id = identity(booking, "id")
                if booking_id in seen_bookings:
                    raise IntegrationError("FreeTour returned the same booking more than once.")
                seen_bookings.add(booking_id)
            snapshot.append((event, bookings))
        progress("fetching", len(months) + current, total_steps,
                 f"Loaded booking days ({current} of {len(dates)})…")
    return snapshot


def run_sync(window="full", run_id=None):
    with freetour_sync_lock() as acquired:
        if not acquired:
            return False
        connection, _ = Connection.objects.get_or_create(vendor="freetour")
        if not connection.enabled or not read_freetour_cookies():
            return False
        run_id = str(run_id or uuid.uuid4().hex)
        now = timezone.now()
        attempt_field = f"{window}_last_attempt_at" if window in {"today", "tomorrow", "future"} else "last_attempt_at"
        Connection.objects.filter(pk=connection.pk).update(
            sync_status="running", sync_run_id=run_id, sync_reserved_at=now,
            detail="", **{attempt_field: now},
        )

        last_progress = {"current": 0, "total": 0, "message": "Starting FreeTour sync…"}

        def progress(phase, current=0, total=0, message=""):
            last_progress.update(current=current, total=total, message=message)
            try:
                write_freetour_sync_progress(phase, current, total, message, run_id=run_id)
            except OSError:
                pass

        def run_active():
            return Connection.objects.filter(
                pk=connection.pk, sync_run_id=run_id, sync_status="running",
            ).exists()

        def should_abort(_current_run_id=None):
            return freetour_cancel_requested(run_id) or not run_active()

        def finish(status, detail="", **updates):
            return Connection.objects.filter(
                pk=connection.pk, sync_run_id=run_id, sync_status="running",
            ).update(sync_status=status, detail=detail, sync_reserved_at=None, **updates)

        progress("starting", message="Starting FreeTour sync…")
        try:
            if should_abort(run_id):
                raise SyncCancelled("Sync canceled. Existing data was kept.")
            client = FreeTourClient()
            progress("fetching", 0, 1, "Loading FreeTour bookings…")
            snapshot = fetch_snapshot(
                client, window=window, progress=progress,
                should_cancel=should_abort, run_id=run_id,
            )
            if should_abort(run_id):
                raise SyncCancelled("Sync canceled. Existing data was kept.")
            total_bookings = sum(len(bookings) for _, bookings in snapshot)
            with transaction.atomic():
                imported = apply_snapshot(
                    connection, snapshot, should_cancel=should_abort,
                    progress=progress, run_id=run_id, create_products=False,
                )
                unmapped = VendorTour.objects.filter(
                    connection=connection, product__isnull=True,
                ).count()
                completed = timezone.now()
                values = {
                    "sync_status": "ok", "sync_reserved_at": None,
                    "last_sync_at": completed, "imported_count": imported,
                    "auth_status": "ok", "auth_checked_at": completed,
                    "detail": "",
                }
                if window in {"today", "tomorrow", "future"}:
                    values[f"{window}_last_sync_at"] = completed
                Connection.objects.filter(pk=connection.pk, sync_run_id=run_id).update(**values)
            clear_freetour_cancel(run_id)
            progress("complete", imported, total_bookings,
                     f"Updated {imported} booking" + ("s" if imported != 1 else "") + ".")
            return True
        except SyncCancelled:
            progress("cancelled", last_progress["current"], last_progress["total"],
                     "Sync canceled. Existing data was kept.")
            finish("cancelled")
            clear_freetour_cancel(run_id)
        except FreeTourAuthenticationError as exc:
            progress("failed", last_progress["current"], last_progress["total"], str(exc))
            finish("failed", str(exc), auth_status="failed", auth_checked_at=timezone.now(), enabled=False)
        except IntegrationError as exc:
            progress("failed", last_progress["current"], last_progress["total"], str(exc))
            finish("failed", str(exc))
        except Exception:
            detail = "FreeTour sync could not complete. Existing bookings were kept."
            progress("failed", last_progress["current"], last_progress["total"], detail)
            finish("failed", detail)
    return False
