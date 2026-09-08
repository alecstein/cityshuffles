from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="MessageTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=160)),
                ("description", models.CharField(blank=True, max_length=240)),
                ("body", models.TextField()),
                ("channel", models.CharField(choices=[("any", "Any channel"), ("sms", "SMS"), ("whatsapp", "WhatsApp"), ("email", "Email")], default="any", max_length=20)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name", "pk"]},
        ),
    ]
