from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("message_templates", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="messagetemplate",
            name="description",
        ),
    ]
