"""Read-only adapter for the provider endpoints observed in the browser.

No login replay, cookie harvesting, or outbound guest messages.
"""
import json
import re
import subprocess
import sys
import threading
import time
import uuid
import queue
from concurrent.futures import FIRST_COMPLETED, Future, wait
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from bookings.models import Guest, Tour, TourProduct
from messaging.models import Contact, Conversation
from .credentials import (cancel_requested, clear_cancel, read_token, sync_lock,
                           write_sync_diagnostic, write_sync_progress)
from .models import Connection, VendorBooking, VendorEvent, VendorGuest, VendorTour


class IntegrationError(Exception):
    def __init__(self, message, code="integration_error"):
        super().__init__(message)
        self.code = code


class AuthenticationError(IntegrationError):
    pass


class SyncCancelled(IntegrationError):
    pass


class TransportError(Exception):
    def __init__(self, kind, status=None):
        super().__init__(kind)
        self.kind = kind
        self.status = status


def _noop_progress(*args, **kwargs):
    return None


class _DaemonExecutor:
    """Small bounded executor for isolated HTTP request processes.

    The coordinator terminates active transport processes before joining these
    daemon threads on cancellation or failure. The threads never touch Django
    state.
    """

    def __init__(self, max_workers):
        self._queue = queue.Queue()
        self._shutdown = False
        self._lock = threading.Lock()
        self._threads = []
        for index in range(max_workers):
            thread = threading.Thread(target=self._worker, name=f"guruwalk-fetch-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            future, function, args = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = function(*args)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)

    def submit(self, function, *args):
        future = Future()
        with self._lock:
            if self._shutdown:
                raise RuntimeError("Executor has been shut down")
            self._queue.put((future, function, args))
        return future

    def shutdown(self, wait=False, cancel_futures=False):
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            if cancel_futures:
                retained = []
                while True:
                    try:
                        item = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if item is None:
                        continue
                    future, function, args = item
                    if not future.cancel():
                        retained.append(item)
                for item in retained:
                    self._queue.put(item)
            for _ in self._threads:
                self._queue.put(None)
        if wait:
            for thread in self._threads:
                thread.join()


class GuruWalkClient:
    def __init__(self, token, request_timeout=20, run_id=None):
        self.token = token
        self.request_timeout = max(0.001, float(request_timeout))
        self.request_count = 0
        self.run_id = str(run_id or "")
        self._request_count_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._active_processes = set()
        self.should_cancel = None
        self.authenticated_at = None

    def set_cancel_check(self, callback):
        self.should_cancel = callback

    def cancel_active_requests(self):
        with self._active_lock:
            processes = tuple(self._active_processes)
        for process in processes:
            try:
                process.kill()
            except OSError:
                pass

    def _transport(self, endpoint, params, deadline):
        try:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("guruwalk_transport.py"))],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            raise TransportError("worker_failure") from None
        with self._active_lock:
            self._active_processes.add(process)
        command = json.dumps({
            "endpoint": endpoint,
            "params": params,
            "token": self.token,
            "timeout": max(0.001, deadline - time.monotonic()),
        }).encode()
        first_wait = True
        try:
            while True:
                if self.should_cancel and self.should_cancel():
                    process.kill()
                    process.communicate()
                    raise TransportError("cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.communicate()
                    raise TransportError("timeout")
                try:
                    stdout, _ = process.communicate(
                        input=command if first_wait else None,
                        timeout=min(0.1, remaining),
                    )
                    break
                except subprocess.TimeoutExpired:
                    first_wait = False
            if process.returncode or len(stdout) > 5_100_000:
                raise TransportError("worker_failure")
            try:
                result = json.loads(stdout)
            except (TypeError, ValueError, UnicodeError):
                raise TransportError("worker_failure") from None
            if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
                raise TransportError("worker_failure")
            if not result["ok"]:
                raise TransportError(result.get("error", "worker_failure"), result.get("status"))
            return result.get("payload")
        finally:
            with self._active_lock:
                self._active_processes.discard(process)
            if process.poll() is None:
                process.kill()
                process.communicate()

    def get(self, endpoint, params):
        with self._request_count_lock:
            self.request_count += 1
            request_number = self.request_count
        request_started = time.monotonic()
        deadline = time.monotonic() + self.request_timeout
        outcome = "unexpected"
        try:
            try:
                payload = self._transport(endpoint, params, deadline)
            except TransportError as exc:
                outcome = exc.kind
                if exc.kind == "cancelled":
                    raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.") from None
                if exc.kind == "timeout":
                    raise IntegrationError("A GuruWalk request timed out. No bookings were changed; try again.", "timeout") from None
                if exc.kind == "http" and exc.status in (401, 403):
                    raise AuthenticationError("GuruWalk rejected access. Reconnect your account.", "authentication") from None
                if exc.kind == "http" and exc.status == 429:
                    raise IntegrationError("GuruWalk is rate limiting requests. No bookings were changed; try again later.", "rate_limited") from None
                if exc.kind == "http":
                    raise IntegrationError("GuruWalk is temporarily unavailable. No bookings were changed; try again.", f"http_{exc.status}") from None
                if exc.kind == "network":
                    raise IntegrationError("Could not reach GuruWalk. No bookings were changed; try again.", "network") from None
                if exc.kind == "response_too_large":
                    raise IntegrationError("GuruWalk returned too much data. No bookings were changed.", "response_too_large") from None
                raise IntegrationError("GuruWalk returned an unexpected response. No bookings were changed.", exc.kind) from None
            if not isinstance(payload, dict):
                outcome = "invalid_payload"
                raise IntegrationError("GuruWalk returned an unexpected response. No bookings were changed.", "invalid_payload")
            if payload.get("statusCode") in (401, 403):
                outcome = "authentication"
                raise AuthenticationError("GuruWalk rejected access. Reconnect your account.", "authentication")
            if payload.get("success") is not True or not isinstance(payload.get("data"), dict):
                outcome = "provider_rejected"
                raise IntegrationError("GuruWalk did not confirm a successful response. No bookings were changed.", "provider_rejected")
            outcome = "ok"
            with self._request_count_lock:
                self.authenticated_at = timezone.now()
            return payload["data"]
        finally:
            write_sync_diagnostic(self.run_id, "request", endpoint=endpoint,
                                  request_count=request_number,
                                  outcome=outcome,
                                  elapsed=round(time.monotonic() - request_started, 3))

    def events(self, start, end):
        return rows(self.get("search_events", {
            "startDate": start.isoformat() + "T00:00:00",
            "endDate": end.isoformat() + "T23:59:59",
        }), "events")

    def bookings(self, event_id):
        return rows(self.get("get_event_bookings", {"eventId": event_id}), "bookings")

    def check_auth(self):
        today = timezone.localdate()
        self.events(today, today)


def rows(data, key):
    result = data.get(key)
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise IntegrationError("GuruWalk's response format changed. Sync paused.")
    total = data.get("totalCount")
    if total is not None:
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise IntegrationError("GuruWalk returned an invalid record count. Sync paused.")
        # Search results are expected to be complete.  GuruWalk booking
        # responses observed in the wild can report an active count smaller
        # than the returned history (for example, cancelled bookings).
        if key == "bookings":
            if total > len(result):
                raise IntegrationError("GuruWalk returned an incomplete list. Sync paused; existing bookings are unchanged.")
        elif total != len(result):
            raise IntegrationError("GuruWalk returned an incomplete list. Sync paused; existing bookings are unchanged.")
    for marker in ("hasMore", "has_more", "nextPage", "next_page", "nextCursor", "next_cursor"):
        value = data.get(marker)
        if value is True or (value not in (None, False, "", 0) and marker in data):
            raise IntegrationError("GuruWalk returned paginated data that cannot be verified safely. Sync paused.")
    pagination = data.get("pagination")
    if pagination not in (None, {}, False, ""):
        raise IntegrationError("GuruWalk returned paginated data that cannot be verified safely. Sync paused.")
    for marker in ("page", "pageSize", "page_size", "totalPages", "total_pages"):
        if marker in data and data[marker] not in (None, False, "", 0, 1):
            raise IntegrationError("GuruWalk returned paginated data that cannot be verified safely. Sync paused.")
    ids = [identity(row, "id") for row in result]
    if len(set(ids)) != len(ids):
        raise IntegrationError("GuruWalk returned duplicate IDs. Sync paused.")
    return result


def identity(row, field):
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value) or len(str(value)) > 100:
        raise IntegrationError("A GuruWalk record is missing a valid ID. Sync paused.")
    return str(value)


def count(row, field):
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IntegrationError("A GuruWalk booking has an invalid party size. Sync paused.")
    return value


def departure_time(row):
    try:
        local = datetime.fromisoformat(row["date"] + "T" + row["startTime"])
        if local.tzinfo is not None:
            raise ValueError()
        zone = ZoneInfo("America/New_York")
        # Do not silently guess through the DST gap or repeated hour.
        if local.replace(tzinfo=zone, fold=0).utcoffset() != local.replace(tzinfo=zone, fold=1).utcoffset():
            raise ValueError()
        return local.replace(tzinfo=zone)
    except (KeyError, ValueError, TypeError):
        raise IntegrationError("A GuruWalk event has an invalid or ambiguous New York time.") from None


def phone_number(row):
    number = re.sub(r"[\s().-]", "", str(row.get("phone") or ""))
    prefix = str(row.get("phonePrefix") or "").lstrip("+")
    if not number:
        return None
    value = number if number.startswith("+") else "+" + prefix + number
    return value if re.fullmatch(r"\+[1-9][0-9]{6,14}", value) else None


def sync_bounds(window="full"):
    today = timezone.localdate()
    if window == "today":
        return today, today
    if window == "tomorrow":
        tomorrow = today + timedelta(days=1)
        return tomorrow, tomorrow
    if window == "future":
        return today + timedelta(days=2), today + timedelta(days=settings.GURUWALK_SYNC_DAYS)
    if window == "full":
        return today - timedelta(days=1), today + timedelta(days=settings.GURUWALK_SYNC_DAYS)
    raise IntegrationError("Unknown GuruWalk sync window.")


def _cancelled(check, run_id=None):
    try:
        return bool(check(run_id)) if run_id is not None else bool(check())
    except TypeError:
        # Keep compatibility with small test/caller callbacks accepting no
        # arguments while run-aware production callbacks use the run id.
        return bool(check())


def _run_active(connection, run_id):
    if not run_id:
        return True
    return Connection.objects.filter(
        pk=connection.pk, sync_run_id=str(run_id), sync_status="running",
    ).exists()


def fetch_snapshot(client, should_cancel=cancel_requested, window="full", progress=None,
                   run_id=None, max_concurrency=None):
    progress = progress or _noop_progress
    first, last = sync_bounds(window)
    if _cancelled(should_cancel, run_id):
        raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.")
    if first > last:
        progress("ready", 0, 0, "No departures in this sync window.")
        return []
    # One search request covers the complete selected window.  The search
    # response already contains every event field needed by the importer.
    progress("fetching", 0, 1,
             f"Loading departures for {first:%b} {first.day}–{last:%b} {last.day}…")
    events = {}
    for event in client.events(first, last):
        event_id = identity(event, "id")
        if event_id in events:
            raise IntegrationError("GuruWalk returned duplicate IDs. Sync paused.")
        events[event_id] = event
    if len(events) > 2000:
        raise IntegrationError("Too many events for one sync. Reduce the sync window.")
    total_events = len(events)
    ordered_events = list(events.values())
    validated_events = []
    for event in ordered_events:
        identity(event, "tourId")
        departure_time(event)
        if not isinstance(event.get("title"), str) or not event["title"].strip():
            raise IntegrationError("A GuruWalk event has no tour title.")
        # Participant count is informational only. A zero count can still
        # conceal cancelled booking history, so every event gets a detail
        # request below.
        participant_count = event.get("participantCount")
        if isinstance(participant_count, bool) or not isinstance(participant_count, int) or participant_count < 0:
            raise IntegrationError("A GuruWalk event has an invalid participant count.")
        validated_events.append(event)

    workers = (getattr(settings, "GURUWALK_BOOKING_CONCURRENCY", 4)
               if max_concurrency is None else max_concurrency)
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
        raise IntegrationError("Invalid GuruWalk booking concurrency setting.")
    results = [None] * total_events
    executor = _DaemonExecutor(max_workers=min(workers, max(1, total_events)))
    pending = {}
    next_index = 0
    completed = 0

    # Request processes check only the run-scoped file signal. Database
    # ownership stays on the coordinator thread.
    if hasattr(client, "set_cancel_check"):
        client.set_cancel_check(lambda: cancel_requested(run_id))

    def stop_workers(wait_for_running=False):
        for future in pending:
            future.cancel()
        if hasattr(client, "cancel_active_requests"):
            client.cancel_active_requests()
        executor.shutdown(wait=wait_for_running, cancel_futures=True)

    try:
        while next_index < total_events and len(pending) < workers \
                and not _cancelled(should_cancel, run_id):
            event = validated_events[next_index]
            future = executor.submit(client.bookings, identity(event, "id"))
            pending[future] = next_index
            next_index += 1
        while pending:
            if _cancelled(should_cancel, run_id):
                raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.")
            done, _ = wait(tuple(pending), timeout=0.1, return_when=FIRST_COMPLETED)
            if not done:
                continue
            completed_batch = []
            for future in done:
                index = pending.pop(future)
                bookings = future.result()
                completed_batch.append((index, bookings))
            # Inspect the entire completed batch before replacing any work.
            # If one response is fatal, no later event is submitted after it.
            for index, bookings in completed_batch:
                results[index] = bookings
                completed += 1
                progress("fetching", completed, total_events,
                         f"Loaded bookings for {validated_events[index]['title'][:120]}…")
            while next_index < total_events and len(pending) < workers \
                    and not _cancelled(should_cancel, run_id):
                event = validated_events[next_index]
                next_future = executor.submit(client.bookings, identity(event, "id"))
                pending[next_future] = next_index
                next_index += 1
    except Exception:
        # The real transport processes are terminated before the terminal
        # state is reported. Waiting here only joins the small fetch threads.
        stop_workers(wait_for_running=True)
        raise
    else:
        stop_workers(wait_for_running=True)

    snapshot = []
    seen_bookings = set()
    for event, bookings in zip(validated_events, results):
        for booking in bookings:
            booking_id = identity(booking, "id")
            if booking_id in seen_bookings:
                raise IntegrationError("A booking appears on multiple events. Will retry after GuruWalk settles.")
            seen_bookings.add(booking_id)
            count(booking, "adults")
            count(booking, "children")
            if booking.get("status") not in ("confirmed", "cancelled", "canceled"):
                raise IntegrationError("An unfamiliar GuruWalk booking status needs review.")
            if not isinstance(booking.get("checkedIn"), bool):
                raise IntegrationError("An unfamiliar GuruWalk attendance value needs review.")
        snapshot.append((event, bookings))
    if _cancelled(should_cancel, run_id):
        raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.")
    progress("ready", 0, sum(len(bookings) for _, bookings in snapshot),
             "GuruWalk data loaded. Preparing booking updates…")
    return snapshot


def get_contact(connection, row):
    external = str(row.get("username") or "booking:" + identity(row, "id"))
    mapped = VendorGuest.objects.filter(connection=connection, external_id=external).first()
    if mapped:
        contact = mapped.contact
        Conversation.objects.get_or_create(contact=contact, channel="sms")
        return contact
    phone = phone_number(row)
    # An exact phone match shares the inbox; never merge contacts by name.
    contact = Contact.objects.filter(phone_number=phone).first() if phone else None
    if not contact:
        contact = Contact.objects.create(name=str(row.get("name") or "Guest")[:200], phone_number=phone)
    VendorGuest.objects.create(connection=connection, external_id=external, contact=contact)
    # Create an inbox thread even when GuruWalk supplied no phone number. The
    # channel will be unavailable until staff add a contact method, but the
    # guest remains visible in Messages.
    Conversation.objects.get_or_create(contact=contact, channel="sms")
    return contact


@transaction.atomic
def apply_snapshot(connection, snapshot, should_cancel=cancel_requested, progress=None, run_id=None):
    progress = progress or _noop_progress
    imported = 0
    total_bookings = sum(len(bookings) for _, bookings in snapshot)
    progress("updating", 0, total_bookings,
             f"Updating 0 of {total_bookings} bookings…" if total_bookings else "No bookings to update.")
    for event, bookings in snapshot:
        if _cancelled(should_cancel, run_id) or not _run_active(connection, run_id):
            raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.")
        product_map = VendorTour.objects.filter(connection=connection, external_id=str(event["tourId"])).first()
        if not product_map:
            product = TourProduct.objects.create(name=event["title"][:240])
            product_map = VendorTour.objects.create(connection=connection, external_id=str(event["tourId"]), product=product)
        mapped_event = VendorEvent.objects.filter(connection=connection, external_id=str(event["id"])).first()
        if not mapped_event:
            tour = Tour.objects.create(product=product_map.product, name=event["title"][:240], start_time=departure_time(event))
            mapped_event = VendorEvent.objects.create(connection=connection, external_id=str(event["id"]), departure=tour)
        tour = mapped_event.departure
        tour.name, tour.start_time, tour.product = event["title"][:240], departure_time(event), product_map.product
        tour.save(update_fields=["name", "start_time", "product"])
        mapped_event.source = event
        mapped_event.save(update_fields=["source"])
        for row in bookings:
            if _cancelled(should_cancel, run_id) or not _run_active(connection, run_id):
                raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.")
            booking_name = str(row.get("name") or "Guest").strip()[:120]
            progress("updating", imported, total_bookings,
                     f"Updating {booking_name}…")
            mapped = VendorBooking.objects.filter(connection=connection, external_id=str(row["id"])).first()
            canceled = row["status"] in ("cancelled", "canceled")
            attendance = "canceled" if canceled else ("present" if row["checkedIn"] else "expected")
            if not mapped:
                contact = get_contact(connection, row)
                first, _, last = contact.name.partition(" ")
                guest = Guest.objects.create(contact=contact, first_name=first[:80], last_name=last[:80],
                    email=contact.email, booked_tour=tour, imported=True,
                    attendance=attendance, adults=row["adults"], children=row["children"],
                    original_adults=row["adults"], original_children=row["children"])
                mapped = VendorBooking.objects.create(
                    connection=connection, external_id=str(row["id"]), booking=guest, event=mapped_event,
                )
            guest = mapped.booking
            guest.booked_tour = tour
            guest.original_adults = row["adults"]
            guest.original_children = row["children"]
            if not guest.party_size_overridden:
                guest.adults = row["adults"]
                guest.children = row["children"]
            if not guest.attendance_overridden:
                guest.attendance = attendance
            guest.save(update_fields=[
                "booked_tour", "adults", "children", "original_adults",
                "original_children", "attendance",
            ])
            mapped.source = row  # Retain source counts/status separately from staff edits.
            mapped.event = mapped_event
            mapped.save(update_fields=["source", "event"])
            imported += 1
            progress("updating", imported, total_bookings,
                     f"Updated {booking_name} ({imported} of {total_bookings})")
    if _cancelled(should_cancel, run_id) or not _run_active(connection, run_id):
        raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.")
    # Absence is never interpreted as cancellation or deletion.
    return imported


def _run_sync_locked(connection, window, run_id):
    """Run one claimed sync while the caller owns the process lock."""
    last_progress = {"current": 0, "total": 0, "message": "Starting GuruWalk sync…"}
    started = time.monotonic()

    def progress(phase, current=0, total=0, message=""):
        last_progress.update(current=current, total=total, message=message)
        try:
            owner = Connection.objects.filter(pk=connection.pk, sync_run_id=run_id)
            owns_run = (owner.exists() if phase in {"complete", "failed", "cancelled"}
                        else owner.filter(sync_status="running").exists())
        except Exception:
            owns_run = True
        if not owns_run:
            return
        try:
            write_sync_progress(phase, current, total, message, run_id=run_id)
        except OSError:
            # Progress is helpful, but a status-file failure must not turn a
            # valid provider sync into a failed import.
            pass
        write_sync_diagnostic(run_id, phase, current=current, total=total,
                              elapsed=round(time.monotonic() - started, 3))

    def finish(status, detail="", **updates):
        # Every terminal update is conditional. A stale worker cannot mutate
        # a newer run that reused this Connection row.
        values = {"sync_status": status, "detail": detail,
                  "sync_reserved_at": None, **updates}
        updated = Connection.objects.filter(
            pk=connection.pk, sync_run_id=run_id, sync_status="running",
        ).update(**values)
        if updated:
            try:
                connection.refresh_from_db()
            except Exception:
                pass
            try:
                clear_cancel(run_id)
            except OSError:
                pass
        return bool(updated)

    progress("starting", message="Starting GuruWalk sync…")
    write_sync_diagnostic(run_id, "claimed", window=window)
    try:
        if _cancelled(cancel_requested, run_id):
            raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.")
        token = read_token()
        if not token:
            raise AuthenticationError("No credential saved. Connect GuruWalk.")
        client = GuruWalkClient(token,
                                request_timeout=getattr(settings, "GURUWALK_REQUEST_TIMEOUT", 20),
                                run_id=run_id)
        def should_abort(_current_run_id=None):
            return cancel_requested(run_id) or not _run_active(connection, run_id)

        # The successful search_events request is the authentication check;
        # do not spend a redundant request on check_auth during a sync.
        progress("fetching", message="Loading GuruWalk departures…")
        snapshot = fetch_snapshot(client, should_cancel=should_abort,
                                  window=window, progress=progress, run_id=run_id)
        if should_abort(run_id):
            raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.")
        total_bookings = sum(len(bookings) for _, bookings in snapshot)
        with transaction.atomic():
            imported = apply_snapshot(connection, snapshot,
                                      should_cancel=should_abort,
                                      progress=progress, run_id=run_id)
            if should_abort(run_id):
                raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.")
            completed_at = timezone.now()
            common = {
                "sync_status": "ok",
                "detail": "",
                "auth_status": "ok",
                "auth_checked_at": client.authenticated_at or completed_at,
                "imported_count": imported,
                "last_sync_at": completed_at,
                "sync_reserved_at": None,
            }
            if window in {"today", "tomorrow", "future"}:
                common[f"{window}_last_sync_at"] = completed_at
            else:
                for tier in ("today", "tomorrow", "future"):
                    common[f"{tier}_last_sync_at"] = completed_at
                    common[f"{tier}_last_attempt_at"] = completed_at
            updated = Connection.objects.filter(
                pk=connection.pk, sync_run_id=run_id, sync_status="running",
            ).update(**common)
            if not updated:
                raise SyncCancelled("GuruWalk sync canceled. Existing data was kept.")
        # The import and authoritative terminal status are committed together.
        # Everything below is optional presentation/diagnostic state and must
        # never turn that committed success into a reported failure.
        try:
            connection.refresh_from_db()
            clear_cancel(run_id)
            progress("complete", imported, total_bookings,
                     f"Updated {imported} booking" + ("s" if imported != 1 else "") + ".")
            write_sync_diagnostic(run_id, "complete", request_count=client.request_count,
                                  imported=imported, outcome="ok",
                                  elapsed=round(time.monotonic() - started, 3))
        except Exception:
            pass
        return True
    except SyncCancelled:
        progress("cancelled", last_progress["current"], last_progress["total"],
                 "Sync canceled. Existing data was kept.")
        finish("cancelled", "")
        write_sync_diagnostic(run_id, "cancelled", request_count=locals().get("client", None).request_count
                              if "client" in locals() else 0)
    except AuthenticationError as exc:
        progress("failed", last_progress["current"], last_progress["total"], str(exc))
        finish("failed", str(exc), auth_status="failed", auth_checked_at=timezone.now(), enabled=False)
        write_sync_diagnostic(run_id, "failed", error=exc.__class__.__name__)
    except IntegrationError as exc:
        progress("failed", last_progress["current"], last_progress["total"], str(exc))
        health = {}
        if "client" in locals() and client.authenticated_at:
            health = {"auth_status": "ok", "auth_checked_at": client.authenticated_at}
        finish("failed", str(exc), **health)
        write_sync_diagnostic(run_id, "failed", error=exc.__class__.__name__, reason=exc.code)
    except Exception:
        # Never persist/print arbitrary exceptions: they may contain
        # credentials or personally identifiable information.
        detail = "Sync could not complete. No bookings were changed. Contact an administrator."
        progress("failed", last_progress["current"], last_progress["total"], detail)
        finish("failed", detail)
        write_sync_diagnostic(run_id, "failed", error="unexpected")
    finally:
        try:
            clear_cancel(run_id)
        except OSError:
            pass
    return False


def _claim_run(connection, window, run_id=None):
    """Claim a reserved manual run or reserve a new scheduled run."""
    connection.refresh_from_db()
    if run_id:
        if connection.sync_status != "running" or connection.sync_run_id != str(run_id):
            return None
        return str(run_id)
    if not connection.enabled or connection.sync_status == "running":
        return None
    run_id = uuid.uuid4().hex
    now = timezone.now()
    attempt_field = (f"{window}_last_attempt_at"
                     if window in {"today", "tomorrow", "future"}
                     else "last_attempt_at")
    values = {
        "enabled": True,
        "sync_status": "running",
        "sync_run_id": run_id,
        "sync_reserved_at": now,
        "detail": "",
        attempt_field: now,
    }
    Connection.objects.filter(pk=connection.pk).update(**values)
    return run_id


def run_sync(window="full", run_id=None):
    """Run a reserved one-shot or claim a scheduled sync.

    A child given a run id waits briefly for the launcher to release the flock;
    an unreserved scheduled invocation remains nonblocking and simply yields
    when another process owns the worker lock.
    """
    timeout = getattr(settings, "GURUWALK_SYNC_STARTUP_TIMEOUT", 10) if run_id else 0
    with sync_lock(timeout=timeout) as acquired:
        if not acquired:
            return False
        connection, _ = Connection.objects.get_or_create(vendor="guruwalk")
        claimed = _claim_run(connection, window, run_id=run_id)
        if not claimed:
            return False
        if run_id is None:
            # We own the lifecycle lock while reserving a fresh run, so any
            # cancellation left by an older run can be safely discarded.
            clear_cancel()
        return _run_sync_locked(connection, window, claimed)
