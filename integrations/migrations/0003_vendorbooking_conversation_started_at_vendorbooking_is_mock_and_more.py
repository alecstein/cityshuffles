from django.db import migrations, models


def mark_existing_bookings_seen(apps, schema_editor):
    VendorBooking = apps.get_model("integrations", "VendorBooking")
    VendorBooking.objects.update(is_new=False)


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0002_connection_future_last_attempt_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendorbooking",
            name="conversation_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="vendorbooking",
            name="is_mock",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="vendorbooking",
            name="is_new",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(mark_existing_bookings_seen, migrations.RunPython.noop),
    ]
