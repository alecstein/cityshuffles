from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from bookings.models import Booking, Tour
from messaging.models import Guest, Conversation, Message
from mytours.forms import BookingForm
from mytours.services import update_booking, create_manual_booking


class BookingUpdateTests(TestCase):
    def setUp(self):
        self.old = Tour.objects.create(name="Original", start_time=timezone.now())
        self.new = Tour.objects.create(name="Second departure", start_time=timezone.now())
        self.contact = Guest.objects.create(name="Guest", email="guest@example.com")
        self.guest = Booking.objects.create(first_name="Guest", contact=self.contact, booked_tour=self.old, imported=True, booking_issue="Keep billing note", attendance="present")
        self.chat = Conversation.objects.create(contact=self.contact, channel="email")
        Message.objects.create(conversation=self.chat, direction="in", body="Can I change tours?")

    def form(self, tour):
        form = BookingForm({"name": "Guest", "email": "guest@example.com", "adults": 2, "children": 1, "tour": tour.pk, "special_requests": "Vegetarian", "booking_issue": "Overwrite", "attendance": "canceled"}, guest=self.guest)
        self.assertTrue(form.is_valid(), form.errors)
        return form

    @patch("bookings.services.send_welcome_for_guest")
    def test_move_updates_booking_without_creating_replacement(self, send):
        form = self.form(self.new)
        with self.captureOnCommitCallbacks(execute=True):
            updated = update_booking(self.guest, form)
        self.guest.refresh_from_db()
        self.assertEqual(updated.pk, self.guest.pk)
        self.assertEqual(self.guest.attendance, "present")
        self.assertFalse(self.guest.attendance_overridden)
        self.assertEqual(self.guest.booked_tour, self.new)
        self.assertEqual(self.guest.booking_issue, "Keep billing note")
        self.assertEqual(self.guest.contact, self.contact)
        self.assertEqual(self.guest.special_requests, "Vegetarian")
        self.assertEqual(self.chat.messages.count(), 1)
        send.assert_not_called()
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(update_booking(self.guest, form).pk, self.guest.pk)
        send.assert_not_called()

    def test_normal_edit_preserves_guide_fields(self):
        update_booking(self.guest, self.form(self.old))
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.attendance, "present")
        self.assertEqual(self.guest.booking_issue, "Keep billing note")
        self.assertEqual(Booking.objects.count(), 1)

    def test_add_uses_selected_departure(self):
        guest = create_manual_booking(self.old, self.form(self.new))
        self.assertEqual(guest.booked_tour, self.new)
        self.assertEqual(guest.vendor_label, "Manual/Walk-up")

    def test_unknown_departure_rejected(self):
        form = BookingForm({"name": "Guest", "adults": 1, "children": 0, "tour": 99999}, guest=self.guest)
        self.assertFalse(form.is_valid())
        self.assertIn("tour", form.errors)
