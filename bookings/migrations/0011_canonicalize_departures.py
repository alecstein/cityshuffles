import hashlib
from datetime import timezone as datetime_timezone

from django.db import migrations
from django.db.models import Count


def fingerprint(product_id, start_time):
    instant = start_time.astimezone(datetime_timezone.utc).replace(microsecond=0)
    return hashlib.sha256(f"v1:{product_id}:{instant.isoformat()}".encode()).hexdigest()


def canonicalize(apps, schema_editor):
    Tour = apps.get_model("bookings", "Tour")
    Guest = apps.get_model("bookings", "Guest")
    VendorEvent = apps.get_model("integrations", "VendorEvent")
    Album = apps.get_model("photos", "Album")
    Photo = apps.get_model("photos", "Photo")
    ThankYouAction = apps.get_model("mytours", "ThankYouAction")

    groups = Tour.objects.exclude(product_id=None).values("product_id", "start_time").annotate(
        count=Count("id")
    )
    for group in groups:
        tours = list(Tour.objects.filter(
            product_id=group["product_id"], start_time=group["start_time"]
        ).order_by("id"))
        canonical = tours[0]
        for duplicate in tours[1:]:
            canonical_album = Album.objects.filter(tour_id=canonical.id).first()
            duplicate_album = Album.objects.filter(tour_id=duplicate.id).first()
            canonical_quick_kinds = set(ThankYouAction.objects.filter(
                tour_id=canonical.id, kind__in=("closing", "photos")
            ).values_list("kind", flat=True))
            duplicate_quick_kinds = set(ThankYouAction.objects.filter(
                tour_id=duplicate.id, kind__in=("closing", "photos")
            ).values_list("kind", flat=True))
            if (canonical_album and duplicate_album) or canonical_quick_kinds & duplicate_quick_kinds:
                continue
            if duplicate_album:
                duplicate_album.tour_id = canonical.id
                duplicate_album.save(update_fields=["tour"])
            Guest.objects.filter(booked_tour_id=duplicate.id).update(booked_tour_id=canonical.id)
            VendorEvent.objects.filter(departure_id=duplicate.id).update(departure_id=canonical.id)
            ThankYouAction.objects.filter(tour_id=duplicate.id).update(tour_id=canonical.id)
            duplicate.delete()
        canonical.fingerprint = fingerprint(canonical.product_id, canonical.start_time)
        canonical.save(update_fields=["fingerprint"])


class Migration(migrations.Migration):
    dependencies = [
        ("bookings", "0010_tour_fingerprint"),
        ("integrations", "0005_vendortour_name_alter_vendorevent_departure_and_more"),
        ("photos", "0001_initial"),
        ("mytours", "0003_thankyoudelivery_email_body"),
    ]

    operations = [migrations.RunPython(canonicalize, migrations.RunPython.noop)]
