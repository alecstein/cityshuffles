import re

from django.db import models


class MessageTemplate(models.Model):
    class Channel(models.TextChoices):
        ANY = "any", "Any channel"
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "Email"

    name = models.CharField(max_length=160)
    system_key = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    body = models.TextField(blank=True)
    separate_email = models.BooleanField(default=False)
    email_body = models.TextField(blank=True)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.ANY)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.system_key:
            self.is_active = True
            if kwargs.get("update_fields"):
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"is_active"}
        super().save(*args, **kwargs)

    def render(self, values=None):
        """Replace known placeholders while keeping unknown ones visible for editing."""
        values = values or {}

        def replace(match):
            key = match.group(1)
            return str(values[key]) if key in values else match.group(0)

        return re.sub(r"\{([a-z_][a-z0-9_]*)\}", replace, self.body)
