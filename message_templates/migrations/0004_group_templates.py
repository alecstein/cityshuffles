from django.db import migrations


def seed(apps, schema_editor):
    Template = apps.get_model("message_templates", "MessageTemplate")
    for key, name, body in [
        ("closing", "Closing message", "Thanks, {guest_name}! We hope you enjoyed {tour_name} with {guide_first_name}."),
        ("photos", "Photos", "Hi {guest_name}, here are your photos from {tour_name}: {photos_url}"),
    ]:
        Template.objects.get_or_create(system_key=key, defaults={"name": name, "body": body, "channel": "any", "is_active": True})


class Migration(migrations.Migration):
    dependencies = [("message_templates", "0003_messagetemplate_system_key")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
