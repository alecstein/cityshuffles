import base64
import json
import tempfile
import threading
import time
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import Future

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bookings.models import Guest, Tour, TourProduct
from messaging.models import Contact, Message
from .credentials import (
    cancel_requested,
    parse_freetour_cookie_header,
    parse_token,
    read_secret,
    write_sync_progress,
    read_token,
    request_cancel,
    save_token,
    sync_lock,
)
from .guruwalk import (AuthenticationError, GuruWalkClient, IntegrationError, SyncCancelled, TransportError,
                       apply_snapshot, departure_time, fetch_snapshot, rows, run_sync, sync_bounds)
from .models import Connection, VendorBooking, VendorEvent
from .models import VendorTour
from .freetour import parse_booking_page


EVENT = {"id": "event-1", "tourId": "product-1", "title": "Brooklyn Walk",
         "date": "2026-09-07", "startTime": "09:30", "participantCount": 0}
BOOKING = {"id": "booking-1", "name": "Example Guest", "username": "guest-1",
           "phonePrefix": "49", "phone": "17600000000", "adults": 2, "children": 1,
           "status": "confirmed", "checkedIn": False}


def token(subject="account-1"):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": subject}).encode()).decode().rstrip("=")
    return "eyJhbGciOiJIUzI1NiJ9." + payload + ".test_signature"


class IntegrationTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.settings_override = override_settings(INTEGRATIONS_SECRET_DIR=Path(self.folder.name), GURUWALK_SYNC_DAYS=0)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.connection = Connection.objects.create(enabled=True, account_id="account-1")
        self.user = get_user_model().objects.create_user(username="staff", password="test")
        self.client.force_login(self.user)

    def test_idempotent_import_and_no_welcomes(self):
        with patch("bookings.services.send_message") as send:
            apply_snapshot(self.connection, [(EVENT, [BOOKING])])
            apply_snapshot(self.connection, [(EVENT, [BOOKING])])
            send.assert_not_called()
        self.assertEqual(Guest.objects.count(), 1)
        self.assertEqual(Tour.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 0)
        guest = Guest.objects.get()
        self.assertEqual((guest.adults, guest.children), (2, 1))
        self.assertEqual(guest.contact.phone_number, "+4917600000000")
        vendor_booking = VendorBooking.objects.get()
        self.assertEqual(vendor_booking.source, BOOKING)
        self.assertEqual(vendor_booking.event, VendorEvent.objects.get())

    def test_seed_mock_bookings_works_with_fresh_records(self):
        apply_snapshot(self.connection, [(EVENT, [])])

        call_command("seed_mock_new_bookings", stdout=StringIO())

        mock_bookings = VendorBooking.objects.filter(is_mock=True)
        self.assertEqual(mock_bookings.count(), 5)
        self.assertEqual(
            set(mock_bookings.values_list("booking__original_adults", flat=True)),
            {1, 2},
        )

    def test_repeated_product_and_returning_guest(self):
        second = dict(EVENT, id="event-2", date="2026-09-08")
        booking = dict(BOOKING, id="booking-2")
        apply_snapshot(self.connection, [(EVENT, [BOOKING]), (second, [booking])])
        self.assertEqual(TourProduct.objects.count(), 1)
        self.assertEqual(Tour.objects.count(), 2)
        self.assertEqual(Guest.objects.count(), 2)
        self.assertEqual(Contact.objects.count(), 1)

    def test_staff_edits_preserved_and_source_cancellation_retained(self):
        apply_snapshot(self.connection, [(EVENT, [BOOKING])])
        guest = Guest.objects.get()
        Guest.objects.filter(pk=guest.pk).update(attendance="present", attendance_overridden=True)
        Contact.objects.filter(pk=guest.contact_id).update(name="Edited Name", phone_number=None)
        canceled = dict(BOOKING, status="cancelled", adults=0)
        apply_snapshot(self.connection, [(EVENT, [canceled])])
        guest.refresh_from_db()
        self.assertEqual(guest.attendance, "present")
        self.assertEqual(guest.contact.name, "Edited Name")
        self.assertIsNone(guest.contact.phone_number)
        self.assertEqual(VendorBooking.objects.get().source["status"], "cancelled")

    def test_vendor_party_counts_are_preserved_when_staff_adjusts_actual_count(self):
        apply_snapshot(self.connection, [(EVENT, [BOOKING])])
        guest = Guest.objects.get()
        Guest.objects.filter(pk=guest.pk).update(
            adults=3,
            party_size_overridden=True,
        )
        changed = dict(BOOKING, adults=4, children=2)
        apply_snapshot(self.connection, [(EVENT, [changed])])
        guest.refresh_from_db()
        self.assertEqual(guest.adults, 3)
        self.assertEqual(guest.children, 1)
        self.assertEqual(guest.original_adults, 4)
        self.assertEqual(guest.original_children, 2)

    def test_cancellation_and_rescheduling(self):
        apply_snapshot(self.connection, [(EVENT, [BOOKING])])
        second = dict(EVENT, id="event-2", date="2026-09-08")
        apply_snapshot(self.connection, [(second, [dict(BOOKING, status="cancelled")])])
        self.assertEqual(Guest.objects.count(), 1)
        self.assertEqual(Guest.objects.get().attendance, "canceled")
        self.assertEqual(Guest.objects.get().booked_tour, VendorEvent.objects.get(external_id="event-2").departure)
        apply_snapshot(self.connection, [])
        self.assertEqual(Guest.objects.count(), 1)

    def test_missing_contacts_zero_events_and_dst(self):
        apply_snapshot(self.connection, [(EVENT, [dict(BOOKING, phone="")])])
        self.assertIsNone(Contact.objects.get().phone_number)
        self.assertTrue(Contact.objects.get().conversations.exists())
        self.assertEqual(departure_time(EVENT).utcoffset().total_seconds(), -4 * 3600)
        self.assertEqual(departure_time(dict(EVENT, date="2026-12-07")).utcoffset().total_seconds(), -5 * 3600)
        with self.assertRaises(IntegrationError):
            departure_time(dict(EVENT, date="2026-11-01", startTime="01:30"))

    def test_partial_or_unknown_data_never_applied(self):
        with self.assertRaises(IntegrationError):
            rows({"bookings": [BOOKING], "totalCount": 2}, "bookings")
        with patch.object(GuruWalkClient, "events", return_value=[dict(EVENT, participantCount=1)]), patch.object(
                GuruWalkClient, "bookings", return_value=[dict(BOOKING, status="new-unknown")]):
            with self.assertRaises(IntegrationError):
                fetch_snapshot(GuruWalkClient("fake"))
        self.assertEqual(Guest.objects.count(), 0)

    def test_transaction_rolls_back_bad_snapshot(self):
        with self.assertRaises(KeyError):
            apply_snapshot(self.connection, [(EVENT, [BOOKING, {}])])
        self.assertEqual(Guest.objects.count(), 0)
        self.assertEqual(Tour.objects.count(), 0)

    def test_auth_fail_pauses_and_preserves_bookings(self):
        apply_snapshot(self.connection, [(EVENT, [BOOKING])])
        save_token(token())
        with patch("integrations.guruwalk.fetch_snapshot",
                   side_effect=AuthenticationError("Reconnect GuruWalk.")):
            self.assertFalse(run_sync())
        self.connection.refresh_from_db()
        self.assertFalse(self.connection.enabled)
        self.assertEqual(self.connection.auth_status, "failed")
        self.assertEqual(Guest.objects.count(), 1)

    def test_success_and_network_error_separate_health(self):
        save_token(token())
        with patch.object(GuruWalkClient, "check_auth"), patch(
                "integrations.guruwalk.fetch_snapshot", return_value=[(EVENT, [BOOKING])]):
            self.assertTrue(run_sync())
        self.connection.refresh_from_db()
        previous = self.connection.last_sync_at
        with patch.object(GuruWalkClient, "check_auth"), patch(
                "integrations.guruwalk.fetch_snapshot", side_effect=IntegrationError("Network unavailable.")):
            self.assertFalse(run_sync())
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.last_sync_at, previous)
        self.assertEqual(self.connection.auth_status, "ok")
        self.assertTrue(self.connection.enabled)

    def test_terminal_status_failure_rolls_back_import(self):
        save_token(token())
        from django.db.models.query import QuerySet
        original_update = QuerySet.update

        def fail_success(query, **values):
            if values.get("sync_status") == "ok":
                raise RuntimeError("synthetic final status failure")
            return original_update(query, **values)

        with patch("integrations.guruwalk.fetch_snapshot", return_value=[(EVENT, [BOOKING])]), \
                patch.object(QuerySet, "update", fail_success):
            self.assertFalse(run_sync())
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.sync_status, "failed")
        self.assertEqual(Guest.objects.count(), 0)
        self.assertIn("No bookings were changed", self.connection.detail)

    def test_post_commit_diagnostic_failure_does_not_reverse_success(self):
        save_token(token())
        product = TourProduct.objects.create(name="Brooklyn Walk")
        VendorTour.objects.create(
            connection=self.connection, external_id=EVENT["tourId"],
            name=EVENT["title"], product=product,
        )

        def diagnostic(run_id, phase, **fields):
            if phase == "complete":
                raise OSError("synthetic diagnostic failure")

        with patch("integrations.guruwalk.fetch_snapshot", return_value=[(EVENT, [BOOKING])]), \
                patch("integrations.guruwalk.write_sync_diagnostic", side_effect=diagnostic):
            self.assertTrue(run_sync())
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.sync_status, "ok")
        self.assertEqual(Guest.objects.count(), 1)

    def test_sync_with_current_health_does_not_make_redundant_auth_call(self):
        save_token(token())
        self.connection.auth_status = "ok"
        self.connection.save(update_fields=["auth_status"])
        with patch.object(GuruWalkClient, "check_auth") as check_auth, patch(
                "integrations.guruwalk.fetch_snapshot", return_value=[]):
            self.assertTrue(run_sync())
        check_auth.assert_not_called()

    def test_network_health_error_does_not_reject_valid_credentials(self):
        save_token(token())
        self.connection.auth_status = "ok"
        self.connection.auth_checked_at = timezone.now() - timedelta(hours=1)
        self.connection.save(update_fields=["auth_status", "auth_checked_at"])
        with patch.object(
                GuruWalkClient, "check_auth",
                side_effect=IntegrationError("Could not reach GuruWalk.", "network")):
            response = self.client.get(reverse("integrations:index"))
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.auth_status, "ok")
        self.assertContains(response, "Authentication previously verified")

    def test_private_credential_form_and_cross_account_guard(self):
        with patch.object(GuruWalkClient, "check_auth"):
            response = self.client.post(reverse("integrations:index"), {"token": token()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(read_token(), token())
        self.assertEqual((Path(self.folder.name) / "guruwalk.token").stat().st_mode & 0o777, 0o600)
        with patch.object(GuruWalkClient, "check_auth") as check_auth:
            response = self.client.get(reverse("integrations:index"))
        check_auth.assert_not_called()
        self.assertNotContains(response, token())
        self.assertContains(response, "Authentication working")
        self.assertNotContains(response, "Pause")
        self.assertNotContains(response, "Sync status")
        self.assertNotContains(response, "Bookings in last successful sync")
        self.assertNotContains(response, "The saved credential is never displayed")
        self.assertContains(response, "How to get your bearer token")
        response = self.client.post(reverse("integrations:index"), {"token": token("different-account")})
        self.assertContains(response, "different GuruWalk account")
        self.assertNotContains(response, token("different-account"))
        self.assertEqual(read_token(), token())

    def test_open_checks_auth_and_manual_sync(self):
        save_token(token())
        with patch.object(GuruWalkClient, "check_auth"):
            response = self.client.get(reverse("integrations:index"))
        self.assertEqual(response.status_code, 200)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.auth_status, "ok")
        with patch("integrations.views.subprocess.Popen") as start_sync:
            response = self.client.post(reverse("integrations:sync"))
        self.assertEqual(response.status_code, 302)
        start_sync.assert_called_once()
        self.assertEqual(start_sync.call_args.args[0][-2:], ["run_integrations", "--once"])

    def test_running_sync_page_shows_progress_and_cancel_label(self):
        self.connection.sync_status = "running"
        self.connection.last_attempt_at = timezone.now()
        self.connection.save(update_fields=["sync_status", "last_attempt_at"])
        write_sync_progress("updating", 2, 5, "Updating Example Guest…")
        response = self.client.get(reverse("integrations:index"))
        self.assertContains(response, "Cancel")
        self.assertNotContains(response, "Cancel ×")
        self.assertContains(response, "Updating Example Guest")
        self.assertContains(response, 'max="5"')
        self.assertContains(response, 'value="2"')

    def test_stale_running_sync_is_recovered(self):
        self.connection.sync_status = "running"
        self.connection.last_attempt_at = timezone.now() - timedelta(minutes=6)
        self.connection.save(update_fields=["sync_status", "last_attempt_at"])
        response = self.client.get(reverse("integrations:index"))
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.sync_status, "failed")
        self.assertContains(response, "stopped reporting progress")

    def test_cancel_signal_is_visible_to_running_sync(self):
        self.assertFalse(cancel_requested())
        request_cancel()
        self.assertTrue(cancel_requested())

    def test_tiered_sync_windows(self):
        today = timezone.localdate()
        self.assertEqual(sync_bounds("today"), (today, today))
        self.assertEqual(sync_bounds("tomorrow"), (today + timedelta(days=1), today + timedelta(days=1)))
        with override_settings(GURUWALK_SYNC_DAYS=30):
            self.assertEqual(sync_bounds("future"), (today + timedelta(days=2), today + timedelta(days=30)))

    def test_snapshot_uses_one_search_and_fetches_zero_participant_events(self):
        events = [
            dict(EVENT, id="event-a", participantCount=0),
            dict(EVENT, id="event-b", participantCount=0),
            dict(EVENT, id="event-c", participantCount=3),
        ]

        class FakeClient:
            def __init__(self):
                self.event_calls = []
                self.booking_calls = []

            def events(self, start, end):
                self.event_calls.append((start, end))
                return events

            def bookings(self, event_id):
                self.booking_calls.append(event_id)
                return []

        client = FakeClient()
        with override_settings(GURUWALK_SYNC_DAYS=0, GURUWALK_BOOKING_CONCURRENCY=2):
            snapshot = fetch_snapshot(client)
        self.assertEqual(len(client.event_calls), 1)
        self.assertEqual(sorted(client.booking_calls), ["event-a", "event-b", "event-c"])
        self.assertEqual(len(snapshot), 3)

    def test_booking_total_count_smaller_than_history_is_retained(self):
        historical = [dict(BOOKING, id="booking-a"), dict(BOOKING, id="booking-b")]
        self.assertEqual(rows({"bookings": historical, "totalCount": 1}, "bookings"), historical)
        with self.assertRaises(IntegrationError):
            rows({"bookings": historical, "totalCount": 2, "pagination": {"hasMore": True}}, "bookings")

    def test_fetch_bounds_outstanding_requests(self):
        events = [dict(EVENT, id=f"event-{index}") for index in range(20)]

        class FakeClient:
            def events(self, start, end):
                return events

            def bookings(self, event_id):
                return []

        with patch("integrations.guruwalk._DaemonExecutor") as executor, \
                patch("integrations.guruwalk.wait", side_effect=IntegrationError("stop")):
            executor.return_value.submit.side_effect = lambda *args: Future()
            with self.assertRaises(IntegrationError):
                fetch_snapshot(FakeClient(), should_cancel=lambda: False, max_concurrency=4)
        self.assertEqual(executor.return_value.submit.call_count, 4)

    def test_fetch_cancellation_does_not_wait_for_broken_adapter_thread(self):
        started = threading.Event()
        release = threading.Event()
        events = [dict(EVENT, id=f"event-{i}") for i in range(4)]

        class StalledClient:
            def events(self, start, end):
                return events

            def bookings(self, event_id):
                started.set()
                release.wait(5)
                return []

            def cancel_active_requests(self):
                release.set()

        began = time.monotonic()
        with self.assertRaises(SyncCancelled):
            fetch_snapshot(StalledClient(), should_cancel=lambda: started.is_set(),
                           max_concurrency=4)
        self.assertLess(time.monotonic() - began, 1)
        release.set()

    def test_status_endpoint_is_local_and_run_scoped(self):
        self.connection.sync_status = "running"
        self.connection.sync_run_id = "run-current"
        self.connection.sync_reserved_at = timezone.now()
        self.connection.save(update_fields=["sync_status", "sync_run_id", "sync_reserved_at"])
        write_sync_progress("fetching", 2, 4, "Loading bookings…", run_id="run-current")
        with patch("integrations.views.GuruWalkClient") as client:
            response = self.client.get(reverse("integrations:sync_status"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["current"], 2)
        self.assertEqual(response.json()["run_id"], "run-current")
        self.assertIn("auth_current", response.json())
        client.assert_not_called()

    def test_cancel_post_cannot_target_a_newer_run(self):
        self.connection.sync_status = "running"
        self.connection.sync_run_id = "new-run"
        self.connection.save(update_fields=["sync_status", "sync_run_id"])
        self.client.post(reverse("integrations:cancel"), {"run_id": "old-run"})
        self.assertFalse(cancel_requested("new-run"))
        self.client.post(reverse("integrations:cancel"), {"run_id": "new-run"})
        self.assertTrue(cancel_requested("new-run"))

    def test_success_result_is_visible(self):
        self.connection.sync_status = "ok"
        self.connection.last_sync_at = timezone.now()
        self.connection.imported_count = 7
        self.connection.save(update_fields=["sync_status", "last_sync_at", "imported_count"])
        response = self.client.get(reverse("integrations:index"))
        self.assertContains(
            response,
            '<p class="integration-sync-result integration-ok" data-sync-result>'
            'Sync complete. Updated 7 bookings.</p>',
            html=True,
        )

    def test_manual_launch_passes_run_id_without_holding_lock(self):
        save_token(token())
        with patch.object(GuruWalkClient, "check_auth"):
            self.client.get(reverse("integrations:index"))
        with patch("integrations.views.subprocess.Popen") as start_sync:
            response = self.client.post(reverse("integrations:sync"))
        self.assertEqual(response.status_code, 302)
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.sync_run_id)
        self.assertEqual(start_sync.call_args.kwargs["env"]["CITYSHUFFLES_GURUWALK_RUN_ID"],
                         self.connection.sync_run_id)

    def test_requires_login_csrf_and_safe_errors(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("integrations:index")).status_code, 302)
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("integrations:index"), {"token": token()}).status_code, 403)
        with self.assertRaises(ValueError):
            parse_token("Cookie: something=secret")
        with patch.object(GuruWalkClient, "_transport", side_effect=TransportError("http", 401)):
            with self.assertRaises(AuthenticationError) as error:
                GuruWalkClient("secret").check_auth()
            self.assertNotIn("secret", str(error.exception))

    def test_lock_prevents_concurrent_sync(self):
        with sync_lock() as acquired:
            self.assertTrue(acquired)
            self.assertFalse(run_sync())

    def test_vendor_events_share_canonical_departure(self):
        apply_snapshot(self.connection, [(EVENT, [BOOKING])])
        product = TourProduct.objects.get()
        freetour = Connection.objects.create(vendor="freetour", enabled=True)
        VendorTour.objects.create(
            connection=freetour, external_id="ft-product", name="Brooklyn Walk", product=product,
        )
        ft_event = dict(EVENT, id="ft-event", tourId="ft-product")
        ft_booking = dict(BOOKING, id="ft-booking", username="booking:ft-booking")
        apply_snapshot(freetour, [(ft_event, [ft_booking])], create_products=False)
        self.assertEqual(Tour.objects.count(), 1)
        self.assertEqual(VendorEvent.objects.count(), 2)
        self.assertEqual(VendorEvent.objects.values_list("departure_id", flat=True).distinct().count(), 1)

    def test_freetour_html_parser_keeps_cancelled_children_and_ids(self):
        html = b'''<h1 class="booking-title">Bookings for 2026-09-08</h1>
        <input id="dater" value="2026-09-08">
        <div class="booking-tourcard" data-id="tour-78573">
          <div class="booking-tourcard__title"><span>Example Walk</span></div>
          <input name="id" value="event-44">
          <div class="booking-tourcard__time">2:00 PM</div>
          <div class="booking-tourcard__limit-value"><span>0</span>/<span>infinity</span></div>
          <div class="booking-person booking-person--cancelled" data-booking-id="booking-55">
            <div class="booking-person__name"><span>Example Guest</span></div>
            <span class="adults">2</span><span class="children">1</span>
            <div class="details-block__phone"><span class="details-block_content">+15550000000</span></div>
            <div class="details-block__ref"><span>Ref:</span><span>ABC-55</span></div>
          </div>
        </div>'''
        snapshot = parse_booking_page(html, timezone.localdate().replace(year=2026, month=9, day=8))
        event, bookings = snapshot[0]
        self.assertEqual((event["id"], event["tourId"], event["startTime"]),
                         ("event-44", "78573", "14:00"))
        self.assertEqual((bookings[0]["id"], bookings[0]["adults"], bookings[0]["children"], bookings[0]["status"]),
                         ("booking-55", 2, 1, "cancelled"))

    def test_freetour_cookie_validation_and_mapping_endpoint(self):
        cookies = parse_freetour_cookie_header("Cookie: backoffice=secret; PHPSESSID=session")
        self.assertEqual(cookies["backoffice"], "secret")
        with self.assertRaises(ValueError):
            parse_freetour_cookie_header("unrelated=value")
        mapping = VendorTour.objects.create(connection=self.connection, external_id="tour-x", name="Tour X")
        product = TourProduct.objects.create(name="Canonical Tour")
        response = self.client.post(reverse("integrations:map_vendor_tour"), {
            "mapping_id": mapping.pk, "product_id": product.pk,
        })
        self.assertEqual(response.status_code, 302)
        mapping.refresh_from_db()
        self.assertEqual(mapping.product, product)

    @override_settings(GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="client-secret")
    def test_gmail_oauth_start_and_callback(self):
        response = self.client.get(reverse("integrations:connect_gmail"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])
        state = self.client.session["gmail_oauth_state"]
        with patch("integrations.views.exchange_code", return_value=("access-token", "refresh-token")), \
                patch("integrations.views.GmailClient") as client_class:
            client_class.return_value.profile.return_value = "inbox@example.com"
            response = self.client.get(reverse("integrations:gmail_callback"), {
                "state": state,
                "code": "one-time-code",
            })
        self.assertRedirects(response, reverse("integrations:index"))
        connection = Connection.objects.get(vendor="gmail")
        self.assertEqual(connection.account_id, "inbox@example.com")
        self.assertTrue(connection.enabled)
        self.assertEqual(read_secret("gmail.refresh-token"), "refresh-token")
