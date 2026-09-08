import time
import os
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from integrations.guruwalk import run_sync
from integrations.freetour import run_sync as run_freetour_sync
from integrations.gmail import run_sync as run_gmail_sync
from integrations.models import Connection
from mytours.thank_you import kick_delivery_worker


class Command(BaseCommand):
    help = "Run the GuruWalk and Gmail integration worker. Keep running alongside the web server."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one enabled sync and exit (for a scheduler).")
        parser.add_argument("--run-id", default="", help="Claim a reserved GuruWalk run.")
        parser.add_argument("--vendor", choices=("guruwalk", "freetour"), default="guruwalk")

    def handle(self, *args, **options):
        if options["once"]:
            if options["vendor"] == "freetour":
                run_freetour_sync(
                    run_id=os.environ.get("CITYSHUFFLES_FREETOUR_RUN_ID") or None,
                )
            else:
                run_sync(run_id=options["run_id"] or os.environ.get("CITYSHUFFLES_GURUWALK_RUN_ID") or None)
            return
        self.stdout.write("Integration worker running; checks every 10 minutes. Ctrl-C to stop.")
        try:
            while True:
                close_old_connections()
                kick_delivery_worker()
                connection, _ = Connection.objects.get_or_create(vendor="guruwalk")
                Connection.objects.filter(pk=connection.pk).update(worker_seen_at=timezone.now())
                now = timezone.now()
                if connection.enabled:
                    windows = (
                        ("today", timedelta(minutes=10)),
                        ("tomorrow", timedelta(hours=6)),
                        ("future", timedelta(hours=12)),
                    )
                    for window, interval in windows:
                        attempt = getattr(connection, f"{window}_last_attempt_at")
                        if not attempt or attempt <= now - interval:
                            run_sync(window=window)
                            break
                freetour, _ = Connection.objects.get_or_create(vendor="freetour")
                Connection.objects.filter(pk=freetour.pk).update(worker_seen_at=timezone.now())
                if freetour.enabled:
                    freetour_windows = (
                        ("today", timedelta(minutes=30)),
                        ("tomorrow", timedelta(hours=2)),
                        ("future", timedelta(hours=12)),
                    )
                    for window, interval in freetour_windows:
                        attempt = getattr(freetour, f"{window}_last_attempt_at")
                        if not attempt or attempt <= now - interval:
                            run_freetour_sync(window=window)
                            break
                gmail, _ = Connection.objects.get_or_create(vendor="gmail")
                if (gmail.enabled and
                        (not gmail.last_attempt_at or gmail.last_attempt_at <= now - timedelta(minutes=10))):
                    run_gmail_sync()
                time.sleep(15)
        except KeyboardInterrupt:
            self.stdout.write("Integration worker stopped.")
