from datetime import datetime, time, timedelta
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from bookings.models import Tour, Guest
from messaging.models import Contact, Conversation, Message


class Command(BaseCommand):
    help = "Add sample assigned tours and guests without sending messages."

    def add_arguments(self, parser):
        parser.add_argument("username")

    @transaction.atomic
    def handle(self, *args, **options):
        user = get_user_model().objects.get(username=options["username"])
        today = timezone.localdate()
        tours = [("Central Park Hidden Corners", 0, 10, True),
                 ("Greenwich Village Stories", 0, 14, True),
                 ("Chelsea Art Walk", 1, 11, True),
                 ("Harlem Music History", 3, 13, True),
                 ("DUMBO Waterfront Walk", 2, 10, False)]
        names = [("Nina", "Foster"), ("Theo", "Bennett"), ("Isabel", "Cruz"),
                 ("Felix", "Wong"), ("Amelia", "Stone"), ("Leo", "Grant"),
                 ("Clara", "Evans"), ("Owen", "Silva"), ("Ruby", "Cole"), ("Max", "Rivera")]
        for i, (name, offset, hour, assigned) in enumerate(tours):
            tour, _ = Tour.objects.get_or_create(name=name, defaults={
                "start_time": timezone.make_aware(datetime.combine(today + timedelta(days=offset), time(hour))),
                "responsible": user if assigned else None})
            for j in range(2):
                first, last = names[i*2+j]
                email = f"{first.lower()}.{last.lower()}.demo@example.com"
                contact, _ = Contact.objects.get_or_create(email=email, defaults={"name": f"{first} {last}", "phone_number": f"+121255501{40+i*2+j}"})
                if not Guest.objects.filter(contact=contact).exists():
                    # Demo fixtures bypass welcome signals: never deliver real messages.
                    Guest.objects.bulk_create([Guest(first_name=first, last_name=last, email=email, contact=contact, booked_tour=tour)])
                conversation, _ = Conversation.objects.get_or_create(contact=contact, channel="sms")
                if j == 0 and not conversation.messages.exists():
                    message = Message.objects.create(conversation=conversation, direction="in", body="Hi! Looking forward to the tour. Where should we meet?", is_read=False)
                    conversation.last_message_at = message.created_at
                    conversation.save(update_fields=["last_message_at"])
        self.stdout.write(self.style.SUCCESS(f"Sample tours ready for {user.username}."))
