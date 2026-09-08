from django.core.management.base import BaseCommand
from django.db import transaction

from bookings.models import Guest, Tour, TourProduct
from integrations.models import Connection, VendorBooking, VendorEvent, VendorGuest, VendorTour
from messaging.models import Contact


class Command(BaseCommand):
    help = "Remove DemoTours Inc. sample data only."

    @transaction.atomic
    def handle(self, *args, **options):
        connection = Connection.objects.filter(vendor="demotours").first()
        if not connection:
            self.stdout.write("DemoTours Inc. data was not present.")
            return
        booking_ids = list(VendorBooking.objects.filter(connection=connection).values_list("booking_id", flat=True))
        event_ids = list(VendorEvent.objects.filter(connection=connection).values_list("departure_id", flat=True))
        contact_ids = list(VendorGuest.objects.filter(connection=connection).values_list("contact_id", flat=True))
        VendorBooking.objects.filter(connection=connection).delete()
        Guest.objects.filter(pk__in=booking_ids).delete()
        VendorEvent.objects.filter(connection=connection).delete()
        Tour.objects.filter(pk__in=event_ids).delete()
        VendorGuest.objects.filter(connection=connection).delete()
        Contact.objects.filter(pk__in=contact_ids, bookings__isnull=True).delete()
        VendorTour.objects.filter(connection=connection).delete()
        TourProduct.objects.filter(name__startswith="DemoTours ", departures__isnull=True).delete()
        connection.delete()
        self.stdout.write(self.style.SUCCESS("DemoTours Inc. sample data removed."))
