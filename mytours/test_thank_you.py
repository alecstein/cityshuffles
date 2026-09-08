from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from twilio.base.exceptions import TwilioRestException

from bookings.models import Guest, Tour
from integrations.models import Connection, VendorBooking
from message_templates.models import MessageTemplate
from messaging.models import Contact, Conversation, Message
from messaging.services import SendResult
from .models import ThankYouAction, ThankYouDelivery
from .thank_you import request_thank_you, process_pending_deliveries, retry_delivery


class ThankYouTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="guide", first_name="Jon")
        self.tour = Tour.objects.create(name="Brooklyn Bridge", start_time=timezone.now(), responsible=self.user)
        self.contact = Contact.objects.create(name="Guest One", phone_number="+12125550111", email="guest@example.com")
        self.guest = Guest.objects.create(first_name="Guest", last_name="One", contact=self.contact, booked_tour=self.tour, imported=True)
        self.client.force_login(self.user)

    def test_button_stays_disabled_and_repeat_posts_do_not_duplicate(self):
        url = reverse("mytours:send_thank_you", args=[self.tour.pk])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(ThankYouAction.objects.count(), 1)
        self.assertEqual(ThankYouDelivery.objects.count(), 1)
        response = self.client.get(reverse("mytours:thank_you_status", args=[self.tour.pk]))
        self.assertContains(response, 'disabled>Send thank you message</button>')

    def test_delivery_panel_preserves_collapsed_state_on_poll(self):
        request_thank_you(self.tour, self.user)
        response = self.client.get(reverse("mytours:thank_you_status", args=[self.tour.pk]), {"expanded": "0"})
        self.assertFalse(response.context["delivery_expanded"])
        self.assertContains(response, 'name="expanded" value="0"')

    @patch("mytours.thank_you.send_message", return_value=SendResult("SM-thanks", "sent"))
    def test_last_incoming_channel_wins_and_message_is_personalized(self, send):
        sms = Conversation.objects.create(contact=self.contact, channel="sms")
        email = Conversation.objects.create(contact=self.contact, channel="email")
        Message.objects.create(conversation=sms, direction="in", body="Earlier")
        Message.objects.create(conversation=email, direction="in", body="Latest reply")
        Message.objects.create(conversation=sms, direction="out", body="Later outgoing SMS")
        action, _ = request_thank_you(self.tour, self.user)
        process_pending_deliveries()
        delivery = action.deliveries.get()
        self.assertEqual(delivery.message.conversation.channel, "email")
        self.assertIn("Brooklyn Bridge with Jon", delivery.message.body)
        self.assertIn("Guest One", delivery.message.body)
        self.assertEqual(delivery.status_label, "Sent")
        response = self.client.get(reverse("mytours:thank_you_status", args=[self.tour.pk]))
        self.assertFalse(response.context["thank_you_pending"])
        process_pending_deliveries()
        send.assert_called_once()

    def test_dedupe_contact_and_exclude_canceled(self):
        Guest.objects.create(first_name="Repeat", contact=self.contact, booked_tour=self.tour, imported=True)
        Guest.objects.create(first_name="Canceled", contact=Contact.objects.create(name="Canceled"), booked_tour=self.tour, attendance="canceled", imported=True)
        action, _ = request_thank_you(self.tour, self.user)
        self.assertEqual(action.deliveries.count(), 1)

    @patch("mytours.thank_you.send_message", return_value=SendResult("SM-retry", "sent"))
    def test_missing_contact_can_retry_after_contact_is_added(self, send):
        self.contact.phone_number = self.contact.email = None
        self.contact.save()
        action, _ = request_thank_you(self.tour, self.user)
        process_pending_deliveries()
        delivery = action.deliveries.get()
        self.assertTrue(delivery.can_retry)
        send.assert_not_called()
        self.contact.email = "new@example.com"
        self.contact.save()
        self.assertTrue(retry_delivery(delivery))
        self.assertFalse(retry_delivery(delivery))
        process_pending_deliveries()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status_label, "Sent")
        self.assertFalse(retry_delivery(delivery))

    @override_settings(TWILIO_VALIDATE_WEBHOOKS=False)
    @patch("mytours.thank_you.send_message", side_effect=[SendResult("SM-1", "sent"), SendResult("SM-2", "sent")])
    def test_failed_delivery_callback_retries_only_that_guest(self, send):
        action, _ = request_thank_you(self.tour, self.user)
        process_pending_deliveries()
        callback = reverse("messaging:twilio_status")
        self.client.post(callback, {"MessageSid": "SM-1", "MessageStatus": "undelivered", "ErrorCode": "30003"})
        delivery = action.deliveries.get()
        self.assertEqual(delivery.status_label, "Undelivered")
        self.assertIn("30003", delivery.message.error_message)
        self.assertTrue(retry_delivery(delivery))
        process_pending_deliveries()
        self.client.post(callback, {"MessageSid": "SM-2", "MessageStatus": "delivered"})
        self.client.post(callback, {"MessageSid": "SM-2", "MessageStatus": "sent"})
        delivery.refresh_from_db()
        self.assertEqual(delivery.status_label, "Delivered")
        self.assertFalse(retry_delivery(delivery))
        self.assertEqual(Message.objects.filter(direction="out").count(), 2)

    @patch("mytours.thank_you.send_message", side_effect=TimeoutError())
    def test_ambiguous_timeout_is_not_automatically_retried(self, send):
        action, _ = request_thank_you(self.tour, self.user)
        process_pending_deliveries()
        delivery = action.deliveries.get()
        self.assertEqual(delivery.state, "review")
        self.assertFalse(retry_delivery(delivery))

    @patch("mytours.thank_you.send_message", side_effect=TwilioRestException(400, "test", code=21211))
    def test_provider_rejection_is_recorded_as_retryable(self, send):
        action, _ = request_thank_you(self.tour, self.user)
        process_pending_deliveries()
        delivery = action.deliveries.get()
        self.assertEqual(delivery.status_label, "Failed")
        self.assertTrue(delivery.can_retry)

    @patch("mytours.thank_you.send_message")
    def test_demo_delivery_is_local_even_if_provider_is_connected(self, send):
        VendorBooking.objects.create(connection=Connection.objects.create(vendor="demotours"), booking=self.guest, external_id="demo-1")
        action, _ = request_thank_you(self.tour, self.user)
        process_pending_deliveries()
        send.assert_not_called()
        self.assertEqual(action.deliveries.get().status_label, "Demo — saved locally")

    def test_template_snapshot_and_unknown_placeholders(self):
        template = MessageTemplate.objects.create(name="Tour goodbye", body="Thanks {guest_name} for joining {tour} with {guide}.")
        action, _ = request_thank_you(self.tour, self.user, template)
        template.delete()
        self.assertEqual(action.deliveries.get().body, "Thanks Guest One for joining Brooklyn Bridge with Jon.")
        other_tour = Tour.objects.create(name="Other", start_time=timezone.now(), responsible=self.user)
        Guest.objects.create(first_name="Other", contact=self.contact, booked_tour=other_tour, imported=True)
        bad = MessageTemplate.objects.create(name="Unknown", body="Hello {missing}")
        with self.assertRaises(ValueError):
            request_thank_you(other_tour, self.user, bad)
        self.assertFalse(ThankYouAction.objects.filter(tour=other_tour).exists())

    def test_other_guide_cannot_send_or_retry(self):
        action, _ = request_thank_you(self.tour, self.user)
        self.client.force_login(get_user_model().objects.create_user(username="other"))
        self.assertEqual(self.client.post(reverse("mytours:send_thank_you", args=[self.tour.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("mytours:retry_thank_you", args=[action.deliveries.get().pk])).status_code, 404)
