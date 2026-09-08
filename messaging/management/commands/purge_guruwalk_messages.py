from django.core.management.base import BaseCommand

from messaging.models import Message


class Command(BaseCommand):
    help = "Delete messages explicitly marked as originating from GuruWalk."

    def handle(self, *args, **options):
        messages = Message.objects.filter(source_vendor="guruwalk")
        count = messages.count()
        messages.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {count} GuruWalk message(s)."))
