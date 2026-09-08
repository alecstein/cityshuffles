from django.db import migrations


def migrate_templates(apps, schema_editor):
    Template = apps.get_model("message_templates", "MessageTemplate")
    for template in Template.objects.all():
        if template.channel == "email":
            template.email_body, template.body, template.separate_email = template.body, "", True
        elif template.channel in {"sms", "whatsapp"}:
            template.separate_email = True
        template.channel = "any"
        if template.system_key:
            template.is_active = True
        template.save()
    Template.objects.get_or_create(system_key="opening", defaults={
        "name": "Opening message", "body": "Welcome to CityShuffles! We’re looking forward to having you on the tour.",
        "channel": "any", "is_active": True,
    })


class Migration(migrations.Migration):
    dependencies = [("message_templates", "0005_messagetemplate_email_body_and_more")]
    operations = [migrations.RunPython(migrate_templates, migrations.RunPython.noop)]
