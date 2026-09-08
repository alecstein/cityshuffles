from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from bookings.models import Guest, Tour, TourProduct
from messaging.models import Contact, Conversation, Message
from integrations.models import Connection, VendorBooking, VendorEvent, VendorGuest, VendorTour


class Command(BaseCommand):
    help = "Create DemoTours Inc. events, bookings, contacts, and messages for local UI testing."

    def add_arguments(self, parser):
        parser.add_argument("username", nargs="?", default="alecstein")

    @transaction.atomic
    def handle(self, *args, **options):
        user = get_user_model().objects.get(username=options["username"])
        connection, _ = Connection.objects.get_or_create(vendor="demotours")
        now = timezone.now()
        connection.account_id = "demo-account"
        connection.enabled = True
        connection.auth_status = "ok"
        connection.auth_checked_at = now
        connection.sync_status = "ok"
        connection.last_sync_at = now
        connection.detail = ""
        connection.save()

        tour_data = [
            ("DemoTours Food Crawl", 0, 17),
            ("DemoTours Midtown Stories", 1, 10),
            ("DemoTours SoHo Art Walk", 2, 14),
            ("DemoTours Brooklyn Sunset", 3, 18),
            ("DemoTours Central Park Walk", 5, 11),
        ]
        guest_data = [
            [("Jamie", "Rivera", "+12125550201", 2, 0), ("Priya", "Nair", "+12125550202", 1, 1)],
            [("Marco", "Silva", "+442071838201", 2, 0), ("Tessa", "Lee", None, 1, 0)],
            [("Omar", "Haddad", "+33612345678", 2, 1), ("Grace", "Park", "+12125550206", 1, 0)],
            [("Sophie", "Martin", "+33142345678", 2, 0), ("Evan", "Brooks", "+12125550208", 3, 0)],
            [("Mina", "Khan", "+919876543210", 2, 0), ("Lucas", "Chen", "+12125550210", 1, 1)],
        ]

        for tour_index, (title, day_offset, hour) in enumerate(tour_data, start=1):
            product, _ = TourProduct.objects.get_or_create(name=title)
            VendorTour.objects.update_or_create(
                connection=connection,
                external_id=f"demo-tour-{tour_index}",
                defaults={"product": product},
            )
            departure_time = timezone.make_aware(datetime.combine(
                timezone.localdate() + timedelta(days=day_offset), time(hour)
            ))
            event_source = {"id": f"demo-event-{tour_index}", "tourId": f"demo-tour-{tour_index}",
                            "title": title, "date": departure_time.date().isoformat(),
                            "startTime": departure_time.strftime("%H:%M"), "participantCount": 2}
            event_map = VendorEvent.objects.filter(
                connection=connection, external_id=f"demo-event-{tour_index}"
            ).select_related("departure").first()
            if not event_map:
                event_map = VendorEvent.objects.create(
                    connection=connection,
                    external_id=f"demo-event-{tour_index}",
                    departure=Tour.objects.create(
                        product=product, name=title, start_time=departure_time, responsible=user,
                    ),
                    source=event_source,
                )
            else:
                tour = event_map.departure
                tour.product, tour.name, tour.start_time, tour.responsible = product, title, departure_time, user
                tour.save(update_fields=["product", "name", "start_time", "responsible"])
                event_map.source = event_source
                event_map.save(update_fields=["source"])
            for guest_index, (first, last, phone, adults, children) in enumerate(guest_data[tour_index - 1], start=1):
                external_guest = f"demo-guest-{tour_index}-{guest_index}"
                email = f"{first.lower()}.{last.lower()}@demotours.example"
                contact, _ = Contact.objects.get_or_create(
                    email=email,
                    defaults={"name": f"{first} {last}", "phone_number": phone},
                )
                if contact.name != f"{first} {last}" or contact.phone_number != phone:
                    contact.name, contact.phone_number = f"{first} {last}", phone
                    contact.save(update_fields=["name", "phone_number"])
                vendor_guest, _ = VendorGuest.objects.get_or_create(
                    connection=connection, external_id=external_guest, defaults={"contact": contact},
                )
                conversation, _ = Conversation.objects.get_or_create(contact=contact, channel="sms")
                booking = VendorBooking.objects.filter(
                    connection=connection,
                    external_id=f"demo-booking-{tour_index}-{guest_index}",
                ).select_related("booking").first()
                if not booking:
                    guest = Guest.objects.create(
                        first_name=first, last_name=last, email=email, contact=contact,
                        booked_tour=event_map.departure, imported=True, adults=adults, children=children,
                        original_adults=adults, original_children=children,
                    )
                    booking = VendorBooking.objects.create(
                        connection=connection,
                        external_id=f"demo-booking-{tour_index}-{guest_index}",
                        event=event_map,
                        booking=guest,
                        source={"id": f"demo-booking-{tour_index}-{guest_index}", "status": "confirmed",
                                "adults": adults, "children": children},
                    )
                else:
                    guest = booking.booking
                    guest.booked_tour, guest.imported = event_map.departure, True
                    guest.original_adults, guest.original_children = adults, children
                    if not guest.party_size_overridden:
                        guest.adults, guest.children = adults, children
                    guest.save(update_fields=[
                        "booked_tour", "adults", "children", "original_adults",
                        "original_children", "imported",
                    ])
                    booking.event = event_map
                    booking.save(update_fields=["event"])
                if guest_index == 1 and not conversation.messages.exists():
                    message = Message.objects.create(
                        conversation=conversation, direction=Message.Direction.INCOMING,
                        body="Hi! Looking forward to the DemoTours trip. Where should we meet?",
                        status=Message.Status.RECEIVED, is_read=False,
                        source_vendor="demotours",
                    )
                    conversation.last_message_at = message.created_at
                    conversation.save(update_fields=["last_message_at"])

        self.stdout.write(self.style.SUCCESS("DemoTours Inc. sample data is ready."))
