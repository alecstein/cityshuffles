from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from bookings.models import Guest, Tour
from messaging.models import Contact, Conversation, Message


class MyToursTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="owner")
        self.other = get_user_model().objects.create_user(username="other")
        self.tour = Tour.objects.create(name="Owned tour", start_time=timezone.now(), responsible=self.owner)
        self.hidden = Tour.objects.create(name="Other tour", start_time=timezone.now(), responsible=self.other)
        self.guest = Guest.objects.create(first_name="Guest", last_name="One", contact=Contact.objects.create(name="Guest One"), booked_tour=self.tour)
        self.client.force_login(self.owner)

    def test_only_assigned_tours_visible(self):
        response = self.client.get(reverse("mytours:index"))
        self.assertContains(response, "Owned tour")
        self.assertNotContains(response, f'href="?tour={self.hidden.pk}"')
        self.assertEqual(self.client.get(reverse("mytours:index"), {"tour": self.hidden.pk}).status_code, 404)

    def test_status_updates_are_persistent(self):
        response = self.client.post(reverse("mytours:guest_update", args=[self.guest.pk]), {"attendance": "present"}, HTTP_HX_REQUEST="true")
        self.assertNotContains(response, "Saved")
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.attendance, "present")
        self.client.post(reverse("mytours:guest_update", args=[self.guest.pk]), {"attendance": "canceled"}, HTTP_HX_REQUEST="true")
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.attendance, "canceled")
        self.assertNotContains(response, "Guest notes")

    def test_other_user_cannot_edit_or_read(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("mytours:guest_update", args=[self.guest.pk]), {"attendance": "absent"}).status_code, 404)

    def test_cancel_moves_row_to_bottom_and_restoring_reorders(self):
        other_guest = Guest.objects.create(first_name="Zoe", last_name="Zulu", contact=Contact.objects.create(name="Zoe Zulu"), booked_tour=self.tour)
        url = reverse("mytours:guest_update", args=[self.guest.pk])
        response = self.client.post(url, {"attendance": "canceled"}, HTTP_HX_REQUEST="true")
        self.assertEqual([r["guest"].pk for r in response.context["guest_rows"]], [other_guest.pk, self.guest.pk])
        self.assertContains(response, "booking-canceled")
        response = self.client.post(url, {"attendance": "expected"}, HTTP_HX_REQUEST="true")
        self.assertEqual([r["guest"].pk for r in response.context["guest_rows"]], [self.guest.pk, other_guest.pk])

    def test_empty_date_and_invalid_attendance(self):
        response = self.client.get(reverse("mytours:index"), {"date": "2026-02-01"})
        self.assertContains(response, "No assigned tours on this date.")
        self.assertContains(response, "February 2026")
        self.client.post(reverse("mytours:guest_update", args=[self.guest.pk]), {"attendance": "invalid", "tour_notes": "Bad"})
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.attendance, "expected")

    def test_no_contact_guest_keeps_disabled_send_welcome_action(self):
        response = self.client.get(
            reverse("mytours:index"),
            {"date": timezone.localdate(self.tour.start_time).isoformat(), "tour": self.tour.pk},
        )

        self.assertContains(response, "Send welcome")
        self.assertContains(response, "no contact information")

    def test_add_booking_uses_modal_without_location_field(self):
        response = self.client.get(
            reverse("mytours:index"),
            {"date": timezone.localdate(self.tour.start_time).isoformat(), "tour": self.tour.pk},
        )

        self.assertContains(response, 'class="booking-modal"')
        self.assertContains(response, "Cancel")
        self.assertNotContains(response, "Location")

    def test_started_chat_uses_chat_bubble_action(self):
        self.guest.contact.phone_number = "+12125550005"
        self.guest.contact.save(update_fields=["phone_number"])
        conversation = Conversation.objects.create(
            contact=self.guest.contact,
            channel=Conversation.Channel.SMS,
        )
        Message.objects.create(
            conversation=conversation,
            direction=Message.Direction.INCOMING,
            body="Hello",
        )

        response = self.client.get(
            reverse("mytours:index"),
            {"date": timezone.localdate(self.tour.start_time).isoformat(), "tour": self.tour.pk},
        )

        self.assertContains(response, "View chat with Guest One")
        self.assertNotContains(response, "Send welcome")

    def test_unstarted_guest_has_welcome_and_chat_actions(self):
        self.guest.contact.phone_number = "+12125550005"
        self.guest.contact.save(update_fields=["phone_number"])

        response = self.client.get(
            reverse("mytours:index"),
            {"date": timezone.localdate(self.tour.start_time).isoformat(), "tour": self.tour.pk},
        )

        self.assertContains(response, "Send welcome to Guest One")
        self.assertContains(response, "Open chat with Guest One")

    def test_open_chat_does_not_send_welcome(self):
        self.guest.contact.phone_number = "+12125550005"
        self.guest.contact.save(update_fields=["phone_number"])

        response = self.client.post(
            reverse("bookings:open_guest_conversation", args=[self.guest.pk]),
        )

        conversation = Conversation.objects.get(
            contact=self.guest.contact,
            channel=Conversation.Channel.SMS,
        )
        self.assertRedirects(
            response,
            f"{reverse('messaging:inbox')}?conversation={conversation.pk}",
        )
        self.assertFalse(Message.objects.filter(conversation=conversation).exists())

    def test_status_label_is_not_checked_in(self):
        response = self.client.get(
            reverse("mytours:index"),
            {"date": timezone.localdate(self.tour.start_time).isoformat(), "tour": self.tour.pk},
        )
        self.assertContains(response, 'class="attendance-actions"')
        self.assertNotContains(response, ">Booked<")

    def test_manual_booking_stores_vendor_and_party_source_counts(self):
        response = self.client.post(
            reverse("mytours:add_booking", args=[self.tour.pk]),
            {
                "name": "Walk Up Guest",
                "phone_number": "+12125550123",
                "email": "walkup@example.com",
                "adults": 2,
                "children": 1,
            },
        )
        self.assertRedirects(response, reverse("mytours:index") + f"?date={timezone.localdate(self.tour.start_time).isoformat()}&tour={self.tour.pk}")
        guest = Guest.objects.get(first_name="Walk")
        self.assertTrue(guest.is_manual)
        self.assertEqual((guest.adults, guest.children), (2, 1))
        self.assertEqual((guest.original_adults, guest.original_children), (2, 1))
        from integrations.models import VendorBooking
        booking = VendorBooking.objects.get(booking=guest)
        self.assertEqual(booking.connection.vendor, "manual")
        self.assertNotIn("location", booking.source)

    def test_manual_booking_can_be_added_without_contact_details(self):
        response = self.client.post(
            reverse("mytours:add_booking", args=[self.tour.pk]),
            {"name": "Walk In", "phone_number": "", "email": "", "adults": 1, "children": 0},
        )
        self.assertEqual(response.status_code, 302)
        guest = Guest.objects.get(first_name="Walk", last_name="In")
        self.assertIsNone(guest.contact.phone_number)
        self.assertIsNone(guest.contact.email)
        self.assertFalse(guest.chat_started)

    def test_dashboard_can_add_manual_booking_to_any_visible_tour(self):
        response = self.client.post(
            reverse("bookings:add_booking", args=[self.hidden.pk]),
            {
                "name": "Dashboard Walk Up",
                "phone_number": "+12125550124",
                "email": "dashboard@example.com",
                "adults": 1,
                "children": 2,
            },
        )

        self.assertRedirects(response, reverse("bookings:index"))
        guest = Guest.objects.get(first_name="Dashboard")
        self.assertEqual(guest.booked_tour_id, self.hidden.pk)
        self.assertTrue(guest.is_manual)

    def test_party_counts_preserve_vendor_values_and_feedback_cycles(self):
        self.guest.adults = 2
        self.guest.original_adults = 2
        self.guest.save(update_fields=["adults", "original_adults"])
        response = self.client.post(
            reverse("mytours:guest_party_update", args=[self.guest.pk]),
            {"adults_delta": "1"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.adults, 3)
        self.assertEqual(self.guest.original_adults, 2)
        self.assertTrue(self.guest.party_size_overridden)
        for expected in (Guest.Feedback.UP, Guest.Feedback.DOWN, Guest.Feedback.NONE):
            self.client.post(
                reverse("mytours:guest_feedback", args=[self.guest.pk]),
                HTTP_HX_REQUEST="true",
            )
            self.guest.refresh_from_db()
            self.assertEqual(self.guest.feedback, expected)

    def test_manual_booking_can_be_deleted_from_edit_menu(self):
        guest = Guest.objects.create(
            first_name="Manual",
            last_name="Guest",
            contact=Contact.objects.create(name="Manual Guest", phone_number="+12125550125"),
            booked_tour=self.tour,
            imported=False,
            is_manual=True,
        )
        from integrations.models import Connection, VendorBooking
        connection = Connection.objects.create(vendor="manual", enabled=True)
        VendorBooking.objects.create(
            connection=connection,
            external_id="manual-test",
            booking=guest,
        )
        response = self.client.post(reverse("bookings:guest_delete", args=[guest.pk]))
        self.assertRedirects(response, reverse("bookings:index"))
        self.assertFalse(Guest.objects.filter(pk=guest.pk).exists())
