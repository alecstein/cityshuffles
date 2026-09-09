from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Guest, Conversation, Message
from bookings.models import Booking, Tour
from django.utils import timezone


class NavigationUnreadTests(TestCase):
    def test_badge_counts_unread_incoming_and_requires_login(self):
        url = reverse("messaging:unread_badge")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user(username="badge-preview"))
        chat = Conversation.objects.create(contact=Guest.objects.create(name="Preview"), channel="sms")
        Booking.objects.create(contact=chat.contact, first_name="Preview", imported=True,
                               booked_tour=Tour.objects.create(name="Tour", start_time=timezone.now()))
        incoming = Message.objects.create(conversation=chat, direction="in", body="Hello", is_read=False)
        Message.objects.create(conversation=chat, direction="out", body="Reply", is_read=False)
        self.assertContains(self.client.get(url), 'aria-label="1 unread messages"')
        incoming.is_read = True
        incoming.save(update_fields=["is_read"])
        self.assertNotContains(self.client.get(url), 'class="nav-unread-badge"')
