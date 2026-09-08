from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0011_canonicalize_departures"),
    ]

    operations = [
        migrations.AddField(
            model_name="tourproduct",
            name="description",
            field=models.TextField(blank=True),
        ),
    ]
