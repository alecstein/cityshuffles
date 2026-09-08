from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Contact",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                (
                    "phone_number",
                    models.CharField(
                        help_text="Prefer E.164, e.g. +12125551234.",
                        max_length=40,
                        unique=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["name", "phone_number"]},
        ),
        migrations.CreateModel(
            name="Conversation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "channel",
                    models.CharField(
                        choices=[("sms", "SMS"), ("whatsapp", "WhatsApp")],
                        default="sms",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("closed", "Closed")],
                        default="open",
                        max_length=20,
                    ),
                ),
                ("last_message_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "contact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conversations",
                        to="messaging.contact",
                    ),
                ),
            ],
            options={"ordering": ["-last_message_at", "-created_at"]},
        ),
        migrations.CreateModel(
            name="Message",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "direction",
                    models.CharField(
                        choices=[("in", "Incoming"), ("out", "Outgoing")],
                        max_length=3,
                    ),
                ),
                ("body", models.TextField()),
                (
                    "provider_sid",
                    models.CharField(
                        blank=True,
                        max_length=64,
                        null=True,
                        unique=True,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("received", "Received"),
                            ("local", "Local only"),
                            ("queued", "Queued"),
                            ("accepted", "Accepted"),
                            ("sending", "Sending"),
                            ("sent", "Sent"),
                            ("delivered", "Delivered"),
                            ("undelivered", "Undelivered"),
                            ("failed", "Failed"),
                            ("read", "Read"),
                        ],
                        default="received",
                        max_length=20,
                    ),
                ),
                ("is_read", models.BooleanField(default=False)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="messaging.conversation",
                    ),
                ),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="conversation",
            constraint=models.UniqueConstraint(
                fields=("contact", "channel"),
                name="one_conversation_per_contact_channel",
            ),
        ),
    ]
