from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import MessageTemplate


class MessageTemplateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="staff",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_template_replaces_known_placeholders(self):
        template = MessageTemplate.objects.create(
            name="Follow-up",
            body="Hey {guest_name}, how was {tour_name}? {unknown}",
        )
        self.assertEqual(
            template.render({"guest_name": "Taylor", "tour_name": "SoHo"}),
            "Hey Taylor, how was SoHo? {unknown}",
        )

    def test_staff_can_create_and_view_templates(self):
        response = self.client.post(
            reverse("message_templates:create"),
            {
                "name": "Welcome",
                "body": "Hello {guest_name}",
            },
        )
        self.assertRedirects(response, reverse("message_templates:index"))
        self.assertContains(self.client.get(reverse("message_templates:index")), "Hello {guest_name}")

    def test_staff_can_toggle_a_template_in_place(self):
        template = MessageTemplate.objects.create(name="Optional", body="Hello")
        response = self.client.post(
            reverse("message_templates:toggle_active", args=[template.pk]),
            {},
        )
        self.assertRedirects(response, reverse("message_templates:index"))
        template.refresh_from_db()
        self.assertFalse(template.is_active)

    def test_staff_can_delete_a_template_without_confirmation(self):
        template = MessageTemplate.objects.create(name="Remove me", body="Hello")
        response = self.client.post(
            reverse("message_templates:delete", args=[template.pk]),
        )
        self.assertRedirects(response, reverse("message_templates:index"))
        self.assertFalse(MessageTemplate.objects.filter(pk=template.pk).exists())

    def test_toggle_ajax_does_not_redirect_or_reorder(self):
        first = MessageTemplate.objects.create(name="First", body="Hello")
        second = MessageTemplate.objects.create(name="Second", body="Hello")
        response = self.client.post(
            reverse("message_templates:toggle_active", args=[first.pk]),
            {},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 204)
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(MessageTemplate.objects.filter(pk=second.pk).exists())
