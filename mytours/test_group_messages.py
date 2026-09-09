import uuid
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking, Tour
from messaging.models import Guest
from messaging.services import SendResult
from message_templates.models import MessageTemplate
from .models import ThankYouAction
from .thank_you import process_pending_deliveries, retry_delivery


class GroupMessagesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="group-guide", first_name="Jon")
        self.client.force_login(self.user)
        self.tour = Tour.objects.create(name="Test tour", start_time=timezone.now(), responsible=self.user)
        self.guest = Booking.objects.create(first_name="Amy", booked_tour=self.tour, contact=Guest.objects.create(name="Amy", phone_number="+12125550101"), imported=True)
        self.url = reverse("mytours:send_group_message", args=[self.tour.pk])

    def test_builtin_templates_editable_but_not_deletable(self):
        for key in ("closing", "photos"):
            template = MessageTemplate.objects.get(system_key=key)
            self.assertEqual(self.client.post(reverse("message_templates:delete", args=[template.pk])).status_code, 403)
            self.client.post(reverse("message_templates:edit", args=[template.pk]), {"name": template.name, "channel": "any", "body": "Edited {guest_name}"})
            template.refresh_from_db()
            self.assertEqual(template.body, "Edited {guest_name}")
            self.assertEqual(template.system_key, key)

    def test_custom_messages_repeat_but_same_request_does_not(self):
        key = str(uuid.uuid4())
        for _ in range(2):
            self.client.post(self.url, {"kind": "custom", "body": "Hello {guest_name}", "request_key": key}, HTTP_HX_REQUEST="true")
        self.client.post(self.url, {"kind": "custom", "body": "A second message", "request_key": str(uuid.uuid4())})
        self.assertEqual(ThankYouAction.objects.count(), 2)
        self.assertEqual(ThankYouAction.objects.first().deliveries.get().body, "Hello Amy")

    def test_photos_and_empty_custom_cannot_send(self):
        for data in ({"kind": "photos"}, {"kind": "custom", "body": " ", "request_key": str(uuid.uuid4())}):
            response = self.client.post(self.url, data, HTTP_HX_REQUEST="true")
            self.assertContains(response, 'role="alert"')
        self.assertFalse(ThankYouAction.objects.exists())

    @patch("mytours.thank_you.send_message", return_value=SendResult("SMgroup", "sent"))
    def test_history_statuses_checkmark_and_safe_retries(self, send):
        self.client.post(self.url, {"kind": "closing"})
        self.client.post(self.url, {"kind": "closing"})
        self.assertEqual(ThankYouAction.objects.count(), 1)
        process_pending_deliveries()
        action = ThankYouAction.objects.get()
        self.assertTrue(action.successfully_sent)
        delivery = action.deliveries.select_related("message").get()
        self.assertEqual(delivery.row_color, "pending")
        delivery.message.status = "delivered"
        delivery.message.save()
        self.assertEqual(delivery.row_color, "delivered")
        response = self.client.get(reverse("mytours:group_history", args=[self.tour.pk]), {"expanded": action.pk})
        self.assertContains(response, "group-recipient-delivered")
        self.assertContains(response, 'class="group-history-row" open')
        self.assertContains(response, "Closing message successfully sent")
        self.assertContains(response, '>Closing message <span aria-label="Closing message successfully sent">✓</span></button>')
        self.assertNotContains(response, 'disabled>Closing message')
        delivery.message.status = "failed"
        delivery.message.error_message = "Twilio error 21610"
        delivery.message.save()
        self.assertEqual(delivery.row_color, "failed")
        self.assertFalse(delivery.can_retry)
        self.assertFalse(retry_delivery(delivery))

    def test_status_changes_preserve_visible_order_until_reload(self):
        other = Booking.objects.create(first_name="Zoe", booked_tour=self.tour, contact=Guest.objects.create(name="Zoe"), imported=True)
        order = f"{self.guest.pk},{other.pk},"
        url = reverse("mytours:guest_update", args=[self.guest.pk])
        response = self.client.post(url, {"attendance": "canceled", "row_order": order}, HTTP_HX_REQUEST="true")
        self.assertEqual([r["guest"].pk for r in response.context["guest_rows"]], [self.guest.pk, other.pk])
        self.assertContains(response, 'class="attendance-button is-canceled"')
        response = self.client.get(reverse("mytours:index"), {"tour": self.tour.pk})
        self.assertEqual([r["guest"].pk for r in response.context["guest_rows"]], [other.pk, self.guest.pk])
        response = self.client.post(url, {"attendance": "present", "row_order": f"{other.pk},{self.guest.pk},"}, HTTP_HX_REQUEST="true")
        self.assertEqual([r["guest"].pk for r in response.context["guest_rows"]], [other.pk, self.guest.pk])
        self.assertContains(response, 'class="attendance-button is-checked"')

    def test_other_guide_cannot_read_history_or_send(self):
        self.client.force_login(get_user_model().objects.create_user(username="unassigned"))
        self.assertEqual(self.client.post(self.url, {"kind": "closing"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("mytours:group_history", args=[self.tour.pk])).status_code, 404)

    def test_custom_editor_has_short_send_label_and_placeholder(self):
        response = self.client.get(reverse("mytours:index"), {"tour": self.tour.pk})
        self.assertContains(response, 'placeholder="Send a custom message to this tour…"')
        self.assertNotContains(response, '>Send custom message</button>')
        self.assertContains(response, '<button type="submit">Send</button>')
