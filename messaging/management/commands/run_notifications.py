import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from messaging.push import deliver_pending, configured


class Command(BaseCommand):
    help = "Deliver queued Web Push notifications. Run alongside the web server."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if not configured():
            self.stderr.write("Run setup_push and set WEB_PUSH_SUBJECT to a public https URL or mailto contact.")
            return
        try:
            while True:
                close_old_connections()
                deliver_pending()
                if options["once"]:
                    return
                time.sleep(2)
        except KeyboardInterrupt:
            pass
