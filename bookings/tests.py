from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from messaging.models import Guest, Conversation, Message
from integrations.models import Connection, VendorBooking, VendorEvent

from .models import Booking, Guide, Tour
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
        contact = Guest.objects.create(
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
        Booking.objects.create(
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
        self.assertContains(response, "View chat with Taylor Guest")
        self.assertContains(
            response,
            f'{reverse("messaging:inbox")}?conversation={self.conversation.pk}',
        )
        self.assertNotContains(response, "A quick view of who is joining each tour.")

    def test_dashboard_unread_count_includes_all_channels(self):
        guest = Booking.objects.get(contact=self.conversation.contact)
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
        guest = Booking.objects.get(contact=self.conversation.contact)
        response = self.client.post(
            reverse("bookings:guest_attendance_update", args=[guest.pk]),
            {"attendance": "present"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        guest.refresh_from_db()
        self.assertEqual(guest.attendance, Booking.Attendance.PRESENT)
        self.assertContains(response, "is-checked")

    def test_guest_information_can_be_updated(self):
        guest = Booking.objects.get(email="taylor@example.com")
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
        Guest.objects.create(name="Someone Else", phone_number="+12125550999")
        guest = Booking.objects.get(email="taylor@example.com")
        response = self.client.post(reverse("bookings:guest_update", args=[guest.pk]), {
            "name": "Changed", "phone_number": "+12125550999", "email": "", "adults": 2, "children": 0,
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("phone_number", response.json()["errors"])
        guest.refresh_from_db()
        self.assertEqual(guest.full_name, "Taylor Guest")

    def test_dashboard_prefills_shared_edit_modal_and_requires_manual_flag_for_delete(self):
        guest = Booking.objects.get(email="taylor@example.com")
        guest.special_requests = "Allergic to nuts"
        guest.save(update_fields=["special_requests"])
        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, "Allergic to nuts")
        self.assertContains(response, f'data-action="{reverse("bookings:guest_update", args=[guest.pk])}"')
        self.assertEqual(self.client.post(reverse("bookings:guest_delete", args=[guest.pk])).status_code, 404)

    def test_welcome_uses_sms_when_phone_is_available(self):
        guest = Booking.objects.get(email="taylor@example.com")

        send_welcome_for_guest(guest.pk)

        guest.refresh_from_db()
        self.assertEqual(guest.welcome_channel, Conversation.Channel.SMS)
        self.assertEqual(guest.welcome_status, Booking.WelcomeStatus.LOCAL)
        self.assertIsNotNone(guest.welcome_sent_at)

    def test_new_vendor_booking_starts_conversation_and_sends_welcome(self):
        connection = Connection.objects.create(vendor="guruwalk", enabled=True)
        event = VendorEvent.objects.create(
            connection=connection,
            external_id="event-1",
            departure=self.tour,
        )
        contact = Guest.objects.create(
            name="New Vendor Guest",
            phone_number="+12125550006",
        )
        guest = Booking.objects.create(
            first_name="New Vendor",
            last_name="Guest",
            contact=contact,
            booked_tour=self.tour,
        )
        vendor_booking = VendorBooking.objects.create(
            connection=connection,
            external_id="booking-1",
            booking=guest,
            event=event,
            source={"status": "confirmed"},
        )

        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, "New bookings (1)")
        self.assertContains(response, "Send welcome")
        self.assertContains(response, "Ignore")
        self.assertNotContains(response, "Start all chats")

        response = self.client.post(
            reverse("bookings:start_booking_conversation", args=[vendor_booking.pk]),
        )

        conversation = Conversation.objects.get(
            contact=contact,
            channel=Conversation.Channel.SMS,
        )
        self.assertRedirects(
            response,
            f"{reverse('messaging:inbox')}?conversation={conversation.pk}",
        )
        vendor_booking.refresh_from_db()
        self.assertFalse(vendor_booking.is_new)
        self.assertIsNotNone(vendor_booking.conversation_started_at)
        welcome = Message.objects.filter(
            conversation=conversation,
            direction=Message.Direction.OUTGOING,
        ).get()
        self.assertEqual(welcome.source_vendor, "guruwalk")
        self.assertEqual(welcome.sender_name, "staff")

    def test_new_booking_from_any_vendor_is_shown(self):
        connection = Connection.objects.create(vendor="freetour", enabled=True)
        event = VendorEvent.objects.create(
            connection=connection,
            external_id="freetour-event-1",
            departure=self.tour,
        )
        guest = Booking.objects.create(
            first_name="Free",
            last_name="Tour Guest",
            contact=Guest.objects.create(
                name="Free Tour Guest",
                phone_number="+12125550007",
            ),
            booked_tour=self.tour,
        )
        VendorBooking.objects.create(
            connection=connection,
            external_id="freetour-booking-1",
            booking=guest,
            event=event,
            source={"status": "confirmed"},
        )

        response = self.client.get(reverse("bookings:index"))

        self.assertContains(response, "FreeTour")

    def test_opening_chat_without_a_message_keeps_booking_new(self):
        connection = Connection.objects.create(vendor="freetour", enabled=True)
        event = VendorEvent.objects.create(
            connection=connection,
            external_id="freetour-event-1",
            departure=self.tour,
        )
        guest = Booking.objects.create(
            first_name="Open",
            last_name="Chat",
            contact=Guest.objects.create(
                name="Open Chat",
                email="open-chat@example.com",
            ),
            booked_tour=self.tour,
        )
        vendor_booking = VendorBooking.objects.create(
            connection=connection,
            external_id="freetour-booking-1",
            booking=guest,
            event=event,
            source={"status": "confirmed"},
        )

        response = self.client.post(
            reverse("bookings:open_booking_conversation", args=[vendor_booking.pk]),
        )

        conversation = Conversation.objects.get(contact=guest.contact, channel=Conversation.Channel.EMAIL)
        self.assertRedirects(
            response,
            f"{reverse('messaging:inbox')}?conversation={conversation.pk}",
        )
        vendor_booking.refresh_from_db()
        self.assertTrue(vendor_booking.is_new)
        self.assertIsNone(vendor_booking.conversation_started_at)
        self.assertFalse(Message.objects.filter(conversation=conversation).exists())

        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, "Open Chat")
        self.assertContains(response, "New bookings (1)")

    def test_ignoring_booking_removes_it_from_new_bookings_but_not_tour(self):
        connection = Connection.objects.create(vendor="freetour", enabled=True)
        event = VendorEvent.objects.create(
            connection=connection,
            external_id="freetour-event-1",
            departure=self.tour,
        )
        guest = Booking.objects.create(
            first_name="Ignored",
            last_name="Guest",
            contact=Guest.objects.create(name="Ignored Guest"),
            booked_tour=self.tour,
        )
        vendor_booking = VendorBooking.objects.create(
            connection=connection,
            external_id="freetour-booking-1",
            booking=guest,
            event=event,
            source={"status": "confirmed"},
        )

        response = self.client.post(
            reverse("bookings:ignore_new_booking", args=[vendor_booking.pk]),
        )

        self.assertRedirects(response, reverse("bookings:index"))
        vendor_booking.refresh_from_db()
        self.assertFalse(vendor_booking.is_new)
        self.assertEqual(guest.booked_tour_id, self.tour.pk)
        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, "New bookings (0)")
        self.assertContains(response, "Ignored Guest")

    def test_dashboard_mentions_when_there_are_no_new_bookings(self):
        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, "No new bookings right now.")

    def test_chat_action_depends_on_contact_details_and_messages(self):
        no_contact = Booking.objects.create(
            first_name="No",
            last_name="Contact",
            contact=Guest.objects.create(name="No Contact"),
            booked_tour=self.tour,
        )
        empty_chat_contact = Guest.objects.create(
            name="Empty Chat",
            phone_number="+12125550003",
        )
        empty_chat = Conversation.objects.create(
            contact=empty_chat_contact,
            channel=Conversation.Channel.SMS,
        )
        empty_chat_guest = Booking.objects.create(
            first_name="Empty",
            last_name="Chat",
            contact=empty_chat_contact,
            booked_tour=self.tour,
        )

        response = self.client.get(reverse("bookings:index"))

        self.assertContains(response, 'Send welcome to No Contact (no contact information)')
        self.assertContains(response, 'Send welcome to Empty Chat')
        self.assertNotContains(response, 'View chat with Empty Chat')

        Message.objects.create(
            conversation=empty_chat,
            direction=Message.Direction.INCOMING,
            body="Now this chat has started.",
        )
        response = self.client.get(reverse("bookings:index"))
        self.assertContains(response, 'View chat with Empty Chat')
        self.assertContains(response, 'Send welcome to Empty Chat')

    def test_start_chat_creates_welcome_for_existing_guest_without_messages(self):
        contact = Guest.objects.create(
            name="New Chat Guest",
            phone_number="+12125550004",
        )
        guest = Booking.objects.create(
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
