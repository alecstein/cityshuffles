from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0004_message_sender_name_message_sent_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="source_vendor",
            field=models.CharField(
                blank=True,
                default="",
                help_text="The vendor that originated this message, when known.",
                max_length=32,
            ),
        ),
    ]
