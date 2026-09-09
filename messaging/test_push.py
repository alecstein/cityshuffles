from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from pywebpush import WebPushException
from .models import Guest, Conversation, Message, PushDevice, PushDelivery
from .push import deliver_pending, valid_endpoint
from bookings.models import Booking, Tour
from django.utils import timezone


class PushTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="push-test")
        self.client.force_login(self.user)
        self.device = PushDevice.objects.create(user=self.user, session_key=self.client.session.session_key, endpoint="https://fcm.googleapis.com/fcm/send/test", keys={})
        self.chat = Conversation.objects.create(contact=Guest.objects.create(name="Guest"), channel="sms")
        Booking.objects.create(contact=self.chat.contact, first_name="Guest", imported=True,
                               booked_tour=Tour.objects.create(name="Tour", start_time=timezone.now()))

    def incoming(self):
        return Message.objects.create(conversation=self.chat, body="Private guest message", direction="in", is_read=False)

    def test_incoming_only_and_single_queue_entry(self):
        message = self.incoming()
        message.save()
        Message.objects.create(conversation=self.chat, body="Outgoing", direction="out")
        self.assertEqual(PushDelivery.objects.count(), 1)

    @patch("messaging.push.configured", return_value=True)
    @patch("messaging.push.webpush")
    def test_private_payload_and_read_skip(self, send, configured):
        message = self.incoming()
        deliver_pending()
        self.assertEqual(PushDelivery.objects.get().state, "accepted")
        self.assertNotIn("Private guest message", send.call_args.kwargs["data"])
        self.assertIn(f"conversation={self.chat.pk}", send.call_args.kwargs["data"])
        self.incoming()
        Message.objects.update(is_read=True)
        deliver_pending()
        self.assertEqual(send.call_count, 1)

    @patch("messaging.push.configured", return_value=True)
    @patch("messaging.push.webpush")
    def test_expired_endpoint_removed(self, send, configured):
        from requests import Response
        response = Response()
        response.status_code = 410
        send.side_effect = WebPushException("Gone", response=response)
        self.incoming()
        deliver_pending()
        self.assertFalse(PushDevice.objects.exists())

    def test_logout_detaches_current_device(self):
        self.client.logout()
        self.assertFalse(PushDevice.objects.exists())

    def test_endpoint_allowlist_and_auth(self):
        for endpoint in ["http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://fcm.googleapis.com.evil.example/x", "https://user@fcm.googleapis.com/x"]:
            self.assertFalse(valid_endpoint(endpoint))
        self.assertTrue(valid_endpoint("https://web.push.apple.com/test"))
        self.client.logout()
        self.assertEqual(self.client.post("/notifications/device/", {}, content_type="application/json").status_code, 302)

    def test_hidden_tab_does_not_clear_shared_unread(self):
        message = self.incoming()
        url = reverse("messaging:message_list", args=[self.chat.pk])
        self.client.get(url, HTTP_X_PAGE_VISIBLE="no")
        message.refresh_from_db()
        self.assertFalse(message.is_read)
        self.client.get(url, HTTP_X_PAGE_VISIBLE="yes")
        message.refresh_from_db()
        self.assertTrue(message.is_read)

    @patch("messaging.push_views.configured", return_value=True)
    def test_subscription_registration_and_removal(self, configured):
        import base64
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
        payload = {"endpoint": "https://web.push.apple.com/new-device", "keys": {"p256dh": encode(key), "auth": encode(b"a" * 16)}}
        response = self.client.post("/notifications/device/", payload, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PushDevice.objects.count(), 2)
        response = self.client.post("/notifications/device/", {"endpoint": payload["endpoint"], "action": "remove"}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PushDevice.objects.count(), 1)
