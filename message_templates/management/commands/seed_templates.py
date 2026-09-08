from django.core.management.base import BaseCommand

from message_templates.models import MessageTemplate


DEFAULTS = [
    {
        "name": "Welcome to CityShuffles",
        "channel": MessageTemplate.Channel.ANY,
        "body": "Welcome to CityShuffles! We’re looking forward to having you on the tour.",
    },
    {
        "name": "Post-tour check-in",
        "channel": MessageTemplate.Channel.ANY,
        "body": "Hey {guest_name}, how was your trip with {guide_first_name}?",
    },
    {
        "name": "Thank you",
        "channel": MessageTemplate.Channel.ANY,
        "body": "Thanks for joining {tour_name}, {guest_name}. We hope you enjoyed the experience!",
    },
]


class Command(BaseCommand):
    help = "Create the default reusable message templates."

    def handle(self, *args, **options):
        for values in DEFAULTS:
            MessageTemplate.objects.update_or_create(
                name=values["name"],
                defaults=values,
            )
        self.stdout.write(self.style.SUCCESS("Default message templates ready."))
