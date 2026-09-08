from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from messaging.models import Contact, Conversation, Message
from integrations.models import Connection, VendorBooking, VendorEvent

from .models import Guest, Guide, Tour
from .services import send_welcome_for_guest


class BookingDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="staff",
            password="test-password",
        )
        self.client.force_login(self.user)

        self.tour = Tour.objects.create(
            name="Test Tour",
            start_time=timezone.now(),
        )
        contact = Contact.objects.create(
            name="Taylor Guest",
            phone_number="+12125550001",
        )
        conversation = Conversation.objects.create(
            contact=contact,
            channel=Conversation.Channel.SMS,
        )
        self.conversation = conversation
        Message.objects.create(
            conversation=conversation,
            direction=Message.Direction.INCOMING,
            body="Hello from the tour guest",
        )
        Guest.objects.create(
            first_name="Taylor",
            last_name="Guest",
            email="taylor@example.com",
            contact=contact,
            booked_tour=self.tour,
        )
        Guide.objects.create(
            first_name="Alex",
            last_name="Guide",
        )

    def test_dashboard_shows_account_guides_and_message_jump(self):
        response = self.client.get(reverse("bookings:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Logged in as")
        self.assertNotContains(response, "Registered guides")
        self.assertContains(response, "Taylor Guest")
        self.assertContains(response, "Edit Taylor Guest")
        self.assertContains(response, "1 unread")
        self.assertContains(response, "Open conversation with Taylor Guest")
        self.assertContains(
            response,
            f'{reverse("messaging:inbox")}?conversation={self.conversation.pk}',
        )
        self.assertNotContains(response, "A quick view of who is joining each tour.")

    def test_dashboard_unread_count_includes_all_channels(self):
        guest = Guest.objects.get(contact=self.conversation.contact)
        email = Conversation.objects.create(contact=guest.contact, channel=Conversation.Channel.EMAIL)
        Message.objects.create(conversation=email, direction=Message.Direction.INCOMING, body="Unread email", is_read=False)
        self.assertEqual(guest.unread_count, 2)
        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, 'class="chat-count-badge">2</span>', html=False)
        self.conversation.messages.update(is_read=True)
        self.assertEqual(guest.unread_count, 1)
        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, 'class="chat-count-badge">1</span>', html=False)

    def test_dashboard_attendance_can_be_toggled(self):
        guest = Guest.objects.get(contact=self.conversation.contact)
        response = self.client.post(
            reverse("bookings:guest_attendance_update", args=[guest.pk]),
            {"attendance": "present"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        guest.refresh_from_db()
        self.assertEqual(guest.attendance, Guest.Attendance.PRESENT)
        self.assertContains(response, "is-checked")

    def test_guest_information_can_be_updated(self):
        guest = Guest.objects.get(email="taylor@example.com")
        response = self.client.post(
            reverse("bookings:guest_update", args=[guest.pk]),
            {
                "name": "Updated Guest",
                "phone_number": "",
                "email": "updated@example.com",
                "adults": 2,
                "children": 1,
                "special_requests": "Step-free route please",
            },
        )

        self.assertRedirects(response, reverse("bookings:index"))
        guest.refresh_from_db()
        guest.contact.refresh_from_db()
        self.assertEqual(guest.full_name, "Updated Guest")
        self.assertEqual(guest.email, "updated@example.com")
        self.assertIsNone(guest.contact.phone_number)
        self.assertEqual(guest.contact.email, "updated@example.com")
        self.assertEqual(guest.special_requests, "Step-free route please")
        self.assertEqual((guest.adults, guest.children), (2, 1))
        self.assertEqual((guest.original_adults, guest.original_children), (1, 0))

    def test_edit_contact_collision_is_validation_error_and_preserves_guest(self):
        Contact.objects.create(name="Someone Else", phone_number="+12125550999")
        guest = Guest.objects.get(email="taylor@example.com")
        response = self.client.post(reverse("bookings:guest_update", args=[guest.pk]), {
            "name": "Changed", "phone_number": "+12125550999", "email": "", "adults": 2, "children": 0,
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("phone_number", response.json()["errors"])
        guest.refresh_from_db()
        self.assertEqual(guest.full_name, "Taylor Guest")

    def test_edit_prefills_shared_modal_and_requires_manual_flag_for_delete(self):
        guest = Guest.objects.get(email="taylor@example.com")
        guest.special_requests = "Allergic to nuts"
        guest.save(update_fields=["special_requests"])
        response = self.client.get(reverse("bookings:guest_edit", args=[guest.pk]))
        self.assertTemplateUsed(response, "mytours/partials/add_booking_form.html")
        self.assertContains(response, "Allergic to nuts")
        self.assertEqual(self.client.post(reverse("bookings:guest_delete", args=[guest.pk])).status_code, 404)

    def test_welcome_uses_sms_when_phone_is_available(self):
        guest = Guest.objects.get(email="taylor@example.com")

        send_welcome_for_guest(guest.pk)

        guest.refresh_from_db()
        self.assertEqual(guest.welcome_channel, Conversation.Channel.SMS)
        self.assertEqual(guest.welcome_status, Guest.WelcomeStatus.LOCAL)
        self.assertIsNotNone(guest.welcome_sent_at)

    def test_new_vendor_booking_starts_conversation_and_sends_welcome(self):
        connection = Connection.objects.create(vendor="guruwalk", enabled=True)
        event = VendorEvent.objects.create(
            connection=connection,
            external_id="event-1",
            departure=self.tour,
        )
        guest = Guest.objects.get(email="taylor@example.com")
        vendor_booking = VendorBooking.objects.create(
            connection=connection,
            external_id="booking-1",
            booking=guest,
            event=event,
            source={"status": "confirmed"},
        )

        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, "New bookings")
        self.assertContains(response, "Start chat")

        response = self.client.post(
            reverse("bookings:start_booking_conversation", args=[vendor_booking.pk]),
        )

        self.assertRedirects(
            response,
            f"{reverse('messaging:inbox')}?conversation={self.conversation.pk}",
        )
        vendor_booking.refresh_from_db()
        self.assertFalse(vendor_booking.is_new)
        self.assertIsNotNone(vendor_booking.conversation_started_at)
        welcome = Message.objects.filter(
            conversation=self.conversation,
            direction=Message.Direction.OUTGOING,
        ).get()
        self.assertEqual(welcome.source_vendor, "guruwalk")
        self.assertEqual(welcome.sender_name, "staff")

    def test_dashboard_mentions_when_there_are_no_new_bookings(self):
        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, "No new bookings right now.")

    def test_chat_action_depends_on_contact_details_and_messages(self):
        no_contact = Guest.objects.create(
            first_name="No",
            last_name="Contact",
            contact=Contact.objects.create(name="No Contact"),
            booked_tour=self.tour,
        )
        empty_chat_contact = Contact.objects.create(
            name="Empty Chat",
            phone_number="+12125550003",
        )
        empty_chat = Conversation.objects.create(
            contact=empty_chat_contact,
            channel=Conversation.Channel.SMS,
        )
        empty_chat_guest = Guest.objects.create(
            first_name="Empty",
            last_name="Chat",
            contact=empty_chat_contact,
            booked_tour=self.tour,
        )

        response = self.client.get(reverse("bookings:index"))

        self.assertContains(response, 'Start chat with No Contact (no contact information)')
        self.assertContains(response, 'Start chat with Empty Chat')
        self.assertNotContains(response, 'Open conversation with Empty Chat')

        Message.objects.create(
            conversation=empty_chat,
            direction=Message.Direction.INCOMING,
            body="Now this chat has started.",
        )
        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, 'Open conversation with Empty Chat')
        self.assertNotContains(response, 'Start chat with Empty Chat')

    def test_start_chat_creates_welcome_for_existing_guest_without_messages(self):
        contact = Contact.objects.create(
            name="New Chat Guest",
            phone_number="+12125550004",
        )
        guest = Guest.objects.create(
            first_name="New Chat",
            last_name="Guest",
            contact=contact,
            booked_tour=self.tour,
        )

        response = self.client.post(
            reverse("bookings:start_guest_conversation", args=[guest.pk]),
        )

        conversation = Conversation.objects.get(
            contact=contact,
            channel=Conversation.Channel.SMS,
        )
        self.assertRedirects(
            response,
            f"{reverse('messaging:inbox')}?conversation={conversation.pk}",
        )
        self.assertTrue(
            Message.objects.filter(
                conversation=conversation,
                direction=Message.Direction.OUTGOING,
            ).exists()
        )

    def test_start_all_conversations_processes_new_bookings(self):
        connection = Connection.objects.create(vendor="guruwalk", enabled=True)
        first_event = VendorEvent.objects.create(
            connection=connection,
            external_id="event-1",
            departure=self.tour,
        )
        second_tour = Tour.objects.create(
            name="Second Tour",
            start_time=timezone.now(),
        )
        second_event = VendorEvent.objects.create(
            connection=connection,
            external_id="event-2",
            departure=second_tour,
        )
        first_guest = Guest.objects.get(email="taylor@example.com")
        second_contact = Contact.objects.create(
            name="Second Guest",
            phone_number="+12125550002",
        )
        second_guest = Guest.objects.create(
            first_name="Second",
            last_name="Guest",
            email="second@example.com",
            contact=second_contact,
            booked_tour=second_tour,
            imported=True,
        )
        VendorBooking.objects.create(
            connection=connection,
            external_id="booking-1",
            booking=first_guest,
            event=first_event,
            source={"status": "confirmed"},
        )
        VendorBooking.objects.create(
            connection=connection,
            external_id="booking-2",
            booking=second_guest,
            event=second_event,
            source={"status": "confirmed"},
        )

        response = self.client.post(reverse("bookings:start_all_conversations"))

        self.assertRedirects(response, reverse("bookings:index"))
        self.assertEqual(VendorBooking.objects.filter(is_new=True).count(), 0)
        self.assertEqual(Message.objects.filter(direction=Message.Direction.OUTGOING).count(), 2)
