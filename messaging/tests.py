from django.test import TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from .services import SendResult

from .models import Contact, Conversation, Message


class MessagingTests(TestCase):
    @patch("messaging.views.send_message")
    def test_staff_attribution_is_internal_and_shared(self, send_mock):
        send_mock.return_value = SendResult(None, Message.Status.LOCAL)
        self.user.first_name = "Alex"
        self.user.last_name = "Guide"
        self.user.save()
        self.client.post(reverse("messaging:send", args=[self.conversation.pk]), {"body": "See you soon"})
        message = Message.objects.get()
        self.assertEqual(message.sent_by, self.user)
        self.assertEqual(message.sender_name, "Alex Guide")
        self.assertEqual(send_mock.call_args.args[1], "See you soon")
        colleague = type(self.user).objects.create_user(username="colleague")
        self.client.force_login(colleague)
        response = self.client.get(reverse("messaging:message_list", args=[self.conversation.pk]))
        self.assertContains(response, "Alex Guide")

    def test_inbox_editor_updates_contact_and_disables_missing_phone(self):
        response = self.client.post(reverse("messaging:edit_contact", args=[self.conversation.pk]),
                                    {"name": "Updated Guest", "phone_number": "", "email": "updated@example.com"},
                                    HTTP_HX_REQUEST="true")
        self.assertEqual(response.headers["HX-Refresh"], "true")
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.name, "Updated Guest")
        self.assertIsNone(self.contact.phone_number)
        response = self.client.get(reverse("messaging:conversation", args=[self.conversation.pk]))
        self.assertEqual(sum(not tab["available"] for tab in response.context["channel_tabs"]), 2)
        self.assertContains(response, 'class="contact-editor"')

    def test_inbox_lists_a_contact_once_across_multiple_channels(self):
        whatsapp = Conversation.objects.create(
            contact=self.contact,
            channel=Conversation.Channel.WHATSAPP,
        )
        Message.objects.create(
            conversation=whatsapp,
            direction=Message.Direction.INCOMING,
            body="Latest WhatsApp message",
        )
        response = self.client.get(reverse("messaging:inbox"))
        rows = [
            row for row in response.context["conversation_rows"]
            if row["conversation"].contact_id == self.contact.pk
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["conversation"].pk, whatsapp.pk)

    def test_inbox_orders_active_chats_before_empty_placeholders(self):
        active_contact = Contact.objects.create(
            name="Recently active",
            phone_number="+12125550001",
        )
        active = Conversation.objects.create(
            contact=active_contact,
            channel=Conversation.Channel.SMS,
        )
        Message.objects.create(
            conversation=active,
            direction=Message.Direction.INCOMING,
            body="Latest message",
        )
        empty_contact = Contact.objects.create(
            name="Empty placeholder",
            phone_number="+12125550002",
        )
        Conversation.objects.create(
            contact=empty_contact,
            channel=Conversation.Channel.SMS,
        )

        response = self.client.get(reverse("messaging:inbox"))
        rows = response.context["conversation_rows"]
        active_index = next(i for i, row in enumerate(rows) if row["conversation"].contact_id == active_contact.pk)
        empty_index = next(i for i, row in enumerate(rows) if row["conversation"].contact_id == empty_contact.pk)
        self.assertLess(active_index, empty_index)
        self.assertTrue(rows[active_index]["has_messages"])
        self.assertFalse(rows[empty_index]["has_messages"])

    def test_chat_search_matches_contact_names_and_message_bodies(self):
        name_contact = Contact.objects.create(
            name="Searchable Name",
            phone_number="+12125550006",
        )
        Conversation.objects.create(
            contact=name_contact,
            channel=Conversation.Channel.SMS,
        )
        body_contact = Contact.objects.create(
            name="Different Name",
            phone_number="+12125550007",
        )
        body_conversation = Conversation.objects.create(
            contact=body_contact,
            channel=Conversation.Channel.SMS,
        )
        Message.objects.create(
            conversation=body_conversation,
            direction=Message.Direction.INCOMING,
            body="The guest asked about the museum meeting point.",
        )

        response = self.client.get(
            reverse("messaging:conversation_list"),
            {"q": "Searchable"},
        )
        self.assertContains(response, "Searchable Name")
        self.assertNotContains(response, "Different Name")

        response = self.client.get(
            reverse("messaging:conversation_list"),
            {"q": "museum meeting"},
        )
        self.assertContains(response, "Different Name")
        self.assertNotContains(response, "Searchable Name")

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(
            username="staff",
            password="test-password",
        )
        self.client.force_login(self.user)

        self.contact = Contact.objects.create(
            name="Test Person",
            phone_number="+12125550000",
        )
        self.conversation = Conversation.objects.create(
            contact=self.contact,
            channel=Conversation.Channel.SMS,
        )

    @override_settings(
        TWILIO_ACCOUNT_SID="",
        TWILIO_AUTH_TOKEN="",
        TWILIO_SMS_FROM="",
    )
    def test_send_stores_local_message_without_twilio(self):
        response = self.client.post(
            reverse("messaging:send", args=[self.conversation.pk]),
            {"body": "Hello"},
        )

        self.assertEqual(response.status_code, 200)
        message = Message.objects.get()
        self.assertEqual(message.body, "Hello")
        self.assertEqual(message.status, Message.Status.LOCAL)

    def test_inbox_shows_channel_tabs_and_sms_default_for_plus_one(self):
        response = self.client.get(
            reverse("messaging:inbox"),
            {"conversation": self.conversation.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SMS")
        self.assertContains(response, "WhatsApp")
        self.assertNotContains(response, ">Default<")
        self.assertNotContains(response, "<small>Start</small>", html=True)

    def test_staff_can_open_the_other_channel_for_a_contact(self):
        response = self.client.post(
            reverse(
                "messaging:open_channel",
                args=[self.contact.pk, Conversation.Channel.WHATSAPP],
            )
        )

        whatsapp = Conversation.objects.get(
            contact=self.contact,
            channel=Conversation.Channel.WHATSAPP,
        )
        self.assertRedirects(
            response,
            f"{reverse('messaging:inbox')}?conversation={whatsapp.pk}",
        )

    def test_email_only_contact_disables_phone_channels(self):
        email_contact = Contact.objects.create(
            name="Email Guest",
            email="email.guest@example.com",
        )
        email_conversation = Conversation.objects.create(
            contact=email_contact,
            channel=Conversation.Channel.EMAIL,
        )

        response = self.client.get(
            reverse("messaging:inbox"),
            {"conversation": email_conversation.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email")
        self.assertContains(response, "channel-unavailable")

        blocked = self.client.post(
            reverse(
                "messaging:open_channel",
                args=[email_contact.pk, Conversation.Channel.SMS],
            )
        )
        self.assertEqual(blocked.status_code, 400)

    @override_settings(
        TWILIO_VALIDATE_WEBHOOKS=False,
        TWILIO_AUTH_TOKEN="",
    )
    def test_inbound_webhook_creates_message(self):
        response = self.client.post(
            reverse("messaging:twilio_inbound"),
            {
                "From": "+12125559999",
                "To": "+12125558888",
                "Body": "Inbound hello",
                "MessageSid": "SM123",
            },
        )

        self.assertEqual(response.status_code, 200)
        inbound = Message.objects.get(provider_sid="SM123")
        self.assertEqual(inbound.direction, Message.Direction.INCOMING)
        self.assertEqual(inbound.body, "Inbound hello")
        inbound_contact = inbound.conversation.contact
        self.assertEqual(
            inbound_contact.last_inbound_channel,
            Conversation.Channel.SMS,
        )
        self.assertIsNotNone(inbound_contact.last_inbound_at)
