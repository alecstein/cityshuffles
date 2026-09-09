from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from bookings.models import Tour, Booking
from messaging.models import Guest


class AllToursTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="admin", is_staff=True)
        self.guide = get_user_model().objects.create_user(username="guide")
        self.assigned = Tour.objects.create(name="Assigned departure", start_time=timezone.now(), responsible=self.guide)
        self.unassigned = Tour.objects.create(name="Unassigned departure", start_time=timezone.now())
        self.guest = Booking.objects.create(first_name="Test", last_name="Guest", contact=Guest.objects.create(name="Test Guest"), booked_tour=self.unassigned)
        self.client.force_login(self.admin)

    def test_admin_calendar_reuses_detail_for_any_departure(self):
        response = self.client.get(reverse("mytours:all"), {"tour": self.unassigned.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mytours/index.html")
        self.assertContains(response, "Assigned departure")
        self.assertContains(response, "Guide: Unassigned")
        self.assertContains(response, "Photo Gallery")
        self.assertContains(response, "Group messages")
        self.assertEqual(response.context["selected"], self.unassigned)
        self.assertFalse(self.client.get(reverse("mytours:index")).context["tours"].exists())

    def test_admin_can_use_shared_controls(self):
        for route, data in [("guest_update", {"attendance": "present"}), ("guest_party_update", {"adults_delta": 1}), ("guest_feedback", {})]:
            response = self.client.post(reverse("mytours:" + route, args=[self.guest.pk]), data, HTTP_HX_REQUEST="true")
            self.assertEqual(response.status_code, 200)
        self.guest.refresh_from_db()
        self.assertEqual((self.guest.attendance, self.guest.adults, self.guest.feedback), ("present", 2, "up"))
        self.assertEqual(self.client.get(reverse("mytours:group_history", args=[self.unassigned.pk])).status_code, 200)

    def test_guide_cannot_access_all_tours_or_other_controls(self):
        self.client.force_login(self.guide)
        self.assertEqual(self.client.get(reverse("mytours:all")).status_code, 403)
        self.assertEqual(self.client.get(reverse("mytours:index"), {"tour": self.unassigned.pk}).status_code, 404)
        for route, data in [("guest_update", {"attendance": "present"}), ("guest_party_update", {"adults_delta": 1}), ("guest_feedback", {})]:
            self.assertEqual(self.client.post(reverse("mytours:" + route, args=[self.guest.pk]), data).status_code, 404)
        self.assertEqual(self.client.get(reverse("mytours:group_history", args=[self.unassigned.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("mytours:send_group_message", args=[self.unassigned.pk]), {"kind": "closing"}).status_code, 404)

    def test_dashboard_links_to_departure(self):
        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, reverse("mytours:all") + "?date=" + timezone.localdate().isoformat() + "&tour=" + str(self.unassigned.pk))
        self.assertNotContains(response, '<details class="tour-card booking-tour"')
