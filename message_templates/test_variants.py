from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from bookings.models import Booking, Tour
from bookings.services import send_welcome_for_guest
from messaging.models import Guest, Conversation, Message
from messaging.services import SendResult
from mytours.thank_you import request_thank_you
from .models import MessageTemplate


class VariantTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="guide", first_name="Alec", last_name="Stein")
        self.client.force_login(self.user)
        self.tour = Tour.objects.create(name="Test", start_time=timezone.now(), responsible=self.user)
        self.contact = Guest.objects.create(name="Test guest", email="test@example.com")
        self.guest = Booking.objects.create(first_name="Test", contact=self.contact, booked_tour=self.tour, imported=True)

    def test_core_templates_cannot_disable_or_delete(self):
        for key in ("opening", "closing", "photos"):
            t = MessageTemplate.objects.get(system_key=key)
            self.assertEqual(self.client.post(reverse("message_templates:toggle_active", args=[t.pk])).status_code, 403)
            self.assertEqual(self.client.post(reverse("message_templates:delete", args=[t.pk])).status_code, 403)
            t.is_active = False
            t.save()
            t.refresh_from_db()
            self.assertTrue(t.is_active)

    @patch("bookings.services.send_message", return_value=SendResult(None, "local"))
    def test_opening_uses_shared_body_and_full_sender_name(self, send):
        t = MessageTemplate.objects.get(system_key="opening")
        t.body, t.separate_email, t.email_body = "Text greeting", True, "Email greeting {guest_name}"
        t.save()
        send_welcome_for_guest(self.guest.pk, force=True, sender=self.user)
        self.assertEqual(send.call_args.args[1], "Text greeting")
        self.assertEqual(Message.objects.get().sender_name, "Alec Stein")

    @patch("mytours.thank_you.send_message", return_value=SendResult(None, "sent"))
    def test_closing_requires_a_shared_body(self, send):
        t = MessageTemplate.objects.get(system_key="closing")
        t.body, t.separate_email, t.email_body = "", True, "Email goodbye {guest_name}"
        t.save()
        with self.assertRaises(ValueError):
            request_thank_you(self.tour, self.user, t)
        send.assert_not_called()

    def test_picker_uses_shared_body_for_all_channels_and_poll_updates_channel_badge(self):
        email = Conversation.objects.create(contact=self.contact, channel="email")
        self.contact.phone_number = "+12125550111"
        self.contact.save()
        sms = Conversation.objects.create(contact=self.contact, channel="sms")
        Message.objects.create(conversation=sms, direction="in", body="Unread SMS", is_read=False)
        MessageTemplate.objects.create(name="Shared message", body="For all channels", separate_email=True, email_body="Legacy email")
        email_response = self.client.get(reverse("messaging:conversation", args=[email.pk]))
        self.assertContains(email_response, 'data-template-body="For all channels"')
        sms_response = self.client.get(reverse("messaging:conversation", args=[sms.pk]))
        self.assertContains(sms_response, 'data-template-body="For all channels"')
        Message.objects.filter(conversation=sms).update(is_read=False)
        response = self.client.get(reverse("messaging:message_list", args=[email.pk]))
        self.assertContains(response, 'hx-swap-oob="outerHTML"')
        self.assertContains(response, 'class="channel-unread">1</span>')
        self.assertFalse(Message.objects.get(conversation=sms).is_read)
