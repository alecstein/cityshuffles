from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0003_vendorbooking_conversation_started_at_vendorbooking_is_mock_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="connection",
            name="sync_run_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="connection",
            name="sync_reserved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
