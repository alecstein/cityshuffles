from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from bookings.models import Guest, Guide, Tour
from bookings.services import send_welcome_for_guest
from messaging.models import Contact, Conversation, Message


class Command(BaseCommand):
    help = "Create demo bookings, guides, and conversations."

    def handle(self, *args, **options):
        now = timezone.now().replace(second=0, microsecond=0)
        tour_data = [
            ("Historic Downtown Stories", now + timedelta(hours=2)),
            ("SoHo Art & Design", now + timedelta(hours=5, minutes=30)),
            ("Brooklyn Bridge at Sunset", now + timedelta(days=1, hours=2)),
        ]
        tours = {
            name: Tour.objects.update_or_create(
                name=name,
                defaults={"start_time": start_time},
            )[0]
            for name, start_time in tour_data
        }

        guide_data = [
            ("Elena", "Rossi"),
            ("Marcus", "Reed"),
            ("Jordan", "Kim"),
            ("Priya", "Shah"),
        ]
        for first_name, last_name in guide_data:
            Guide.objects.get_or_create(
                first_name=first_name,
                last_name=last_name,
                defaults={"is_admin": True},
            )

        guest_data = [
            (
                "Maya",
                "Patel",
                "maya.patel@example.com",
                "+12125550111",
                "Historic Downtown Stories",
                [("in", "Hi! Is the meeting point still the same today?")],
            ),
            (
                "Liam",
                "Chen",
                "liam.chen@example.com",
                "+12125550112",
                "Historic Downtown Stories",
                [("in", "Can I bring a small backpack on the tour?")],
            ),
            (
                "Sofia",
                "Rodriguez",
                "sofia.rodriguez@example.com",
                "+12125550113",
                "Historic Downtown Stories",
                [
                    ("in", "Thanks, looking forward to it!"),
                    ("out", "We are looking forward to having you."),
                ],
            ),
            (
                "Ethan",
                "Brooks",
                "ethan.brooks@example.com",
                "+12125550114",
                "SoHo Art & Design",
                [("in", "Is there room for one more person in our group?")],
            ),
            (
                "Chloe",
                "Martin",
                "chloe.martin@example.com",
                "+12125550115",
                "SoHo Art & Design",
                [],
            ),
            (
                "Noah",
                "Williams",
                "noah.williams@example.com",
                "+12125550116",
                "Brooklyn Bridge at Sunset",
                [],
            ),
            (
                "Grace",
                "Miller",
                "grace.miller@example.com",
                None,
                "Brooklyn Bridge at Sunset",
                [],
            ),
        ]

        for first_name, last_name, email, phone, tour_name, items in guest_data:
            full_name = f"{first_name} {last_name}"
            contact_lookup = {"phone_number": phone} if phone else {"email": email}
            contact, _ = Contact.objects.get_or_create(
                defaults={
                    "name": full_name,
                    "phone_number": phone,
                    "email": email,
                },
                **contact_lookup,
            )
            changed_fields = []
            if contact.name != full_name:
                contact.name = full_name
                changed_fields.append("name")
            if contact.email != email:
                contact.email = email
                changed_fields.append("email")
            if contact.phone_number != phone:
                contact.phone_number = phone
                changed_fields.append("phone_number")
            if changed_fields:
                contact.save(update_fields=changed_fields)

            conversation, _ = Conversation.objects.get_or_create(
                contact=contact,
                channel=(
                    Conversation.Channel.SMS
                    if phone
                    else Conversation.Channel.EMAIL
                ),
            )
            guest, _ = Guest.objects.update_or_create(
                email=email,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "contact": contact,
                    "booked_tour": tours[tour_name],
                },
            )
            if not guest.welcome_sent_at:
                send_welcome_for_guest(guest.pk)

            if not conversation.messages.exists():
                last = None
                for direction, body in items:
                    last = Message.objects.create(
                        conversation=conversation,
                        direction=direction,
                        body=body,
                        status=(
                            Message.Status.RECEIVED
                            if direction == Message.Direction.INCOMING
                            else Message.Status.LOCAL
                        ),
                        is_read=(direction == Message.Direction.OUTGOING),
                    )

                if last:
                    conversation.last_message_at = last.created_at
                    conversation.save(update_fields=["last_message_at"])

            last_inbound = conversation.messages.filter(
                direction=Message.Direction.INCOMING,
            ).order_by("-created_at").first()
            if last_inbound:
                contact.last_inbound_channel = conversation.channel
                contact.last_inbound_at = last_inbound.created_at
                contact.save(update_fields=["last_inbound_channel", "last_inbound_at"])

        self.stdout.write(
            self.style.SUCCESS("Demo bookings, guides, and conversations created.")
        )
