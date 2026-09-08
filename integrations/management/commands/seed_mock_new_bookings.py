from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bookings.models import Guest
from messaging.models import Contact, Conversation

from integrations.models import Connection, VendorBooking, VendorEvent, VendorGuest


MOCK_GUESTS = [
    ("Amelia", "Brooks", "+442071838750", "amelia.brooks@mockmail.example"),
    ("Daniel", "Ortiz", "+34915550101", "daniel.ortiz@mockmail.example"),
    ("Priyanka", "Nair", "+919876543210", "priyanka.nair@mockmail.example"),
    ("Luca", "Bianchi", "+390212345678", "luca.bianchi@mockmail.example"),
    ("Harper", "Okafor", None, "harper.okafor@mockmail.example"),
]


class Command(BaseCommand):
    help = "Create five mock GuruWalk bookings for the Dashboard new-bookings table."

    @transaction.atomic
    def handle(self, *args, **options):
        connection = Connection.objects.filter(vendor="guruwalk").first()
        if not connection:
            raise CommandError("The GuruWalk connection does not exist yet.")

        events = list(
            VendorEvent.objects
            .filter(connection=connection)
            .select_related("departure")
            .order_by("departure__start_time", "pk")[:5]
        )
        if not events:
            raise CommandError("Sync or create at least one GuruWalk event first.")

        created = 0
        for index, (first_name, last_name, phone, email) in enumerate(MOCK_GUESTS):
            event = events[index % len(events)]
            vendor_adults = 2 if index in {0, 2} else 1
            vendor_children = 1 if index == 2 else 0
            external_booking_id = f"mock-gw-booking-{index + 1}"
            external_guest_id = f"mock-gw-guest-{index + 1}"
            contact_lookup = {"phone_number": phone} if phone else {"email": email}
            contact, _ = Contact.objects.get_or_create(
                defaults={
                    "name": f"{first_name} {last_name}",
                    "phone_number": phone,
                    "email": email,
                },
                **contact_lookup,
            )
            contact.name = f"{first_name} {last_name}"
            contact.email = email
            contact.phone_number = phone
            contact.save(update_fields=["name", "email", "phone_number"])

            vendor_guest, _ = VendorGuest.objects.get_or_create(
                connection=connection,
                external_id=external_guest_id,
                defaults={"contact": contact},
            )
            if vendor_guest.contact_id != contact.pk:
                vendor_guest.contact = contact
                vendor_guest.save(update_fields=["contact"])

            vendor_booking = VendorBooking.objects.filter(
                connection=connection,
                external_id=external_booking_id,
            ).select_related("booking").first()
            if vendor_booking:
                guest = vendor_booking.booking
                guest.first_name = first_name
                guest.last_name = last_name
                guest.email = email
                guest.contact = contact
                guest.booked_tour = event.departure
                guest.imported = True
                if not guest.party_size_overridden:
                    guest.adults = vendor_adults
                    guest.children = vendor_children
                guest.original_adults = vendor_adults
                guest.original_children = vendor_children
                guest.welcome_channel = ""
                guest.welcome_status = Guest.WelcomeStatus.PENDING
                guest.welcome_sent_at = None
                guest.save(update_fields=[
                    "first_name", "last_name", "email", "contact",
                    "booked_tour", "imported", "adults", "children",
                    "original_adults", "original_children",
                    "welcome_channel", "welcome_status", "welcome_sent_at",
                ])
            else:
                guest = Guest.objects.create(
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    contact=contact,
                    booked_tour=event.departure,
                    imported=True,
                    adults=vendor_adults,
                    children=vendor_children,
                    original_adults=vendor_adults,
                    original_children=vendor_children,
                )

            # Keep the inbox placeholder quiet until staff explicitly starts
            # the conversation from the Dashboard.
            Conversation.objects.get_or_create(
                contact=contact,
                channel=(Conversation.Channel.SMS if phone else Conversation.Channel.EMAIL),
            )
            VendorBooking.objects.update_or_create(
                connection=connection,
                external_id=external_booking_id,
                defaults={
                    "booking": guest,
                    "event": event,
                    "is_new": True,
                    "is_mock": True,
                    "conversation_started_at": None,
                    "source": {
                        "id": external_booking_id,
                        "name": f"{first_name} {last_name}",
                        "adults": guest.adults,
                        "children": guest.children,
                        "status": "confirmed",
                        "mock": True,
                    },
                },
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"{created} mock GuruWalk bookings are ready."))
