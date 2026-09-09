from django.conf import settings
from django.db import models
from django.utils import timezone


CHANNEL_CHOICES = (
    ("sms", "SMS"),
    ("whatsapp", "WhatsApp"),
    ("email", "Email"),
)


class PushDevice(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    session_key = models.CharField(max_length=40)
    endpoint = models.URLField(max_length=2048, unique=True)
    keys = models.JSONField()
    previews = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class PushDelivery(models.Model):
    device = models.ForeignKey(PushDevice, on_delete=models.CASCADE)
    message = models.ForeignKey("Message", null=True, on_delete=models.CASCADE)
    state = models.CharField(max_length=16, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    due_at = models.DateTimeField(default=timezone.now)
    error = models.CharField(max_length=160, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["device", "message"], name="unique_push_message_device")]


class Guest(models.Model):
    """A guest's shared identity and lifetime communication history."""

    class PreferredChannel(models.TextChoices):
        AUTOMATIC = "auto", "Automatic"
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"

    name = models.CharField(max_length=200)
    phone_number = models.CharField(
        max_length=40,
        blank=True,
        null=True,
        unique=True,
        help_text="Prefer E.164, e.g. +12125551234.",
    )
    email = models.EmailField(blank=True, null=True, unique=True)
    preferred_channel = models.CharField(
        max_length=20,
        choices=PreferredChannel.choices,
        default=PreferredChannel.AUTOMATIC,
    )
    last_inbound_channel = models.CharField(
        max_length=20,
        choices=CHANNEL_CHOICES,
        blank=True,
    )
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    whatsapp_opted_in = models.BooleanField(default=False)
    greeted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "phone_number"]

    def __str__(self):
        return self.name or self.email or self.phone_number or "Unnamed contact"

    @property
    def has_attended(self):
        return self.bookings.filter(
            attendance="present", booked_tour__start_time__lt=timezone.now(),
        ).exists()

    @property
    def greeting_eligible(self):
        return not self.greeted_at and not self.has_attended

    @property
    def automatic_channel(self):
        if self.last_inbound_channel:
            return self.last_inbound_channel
        if self.phone_number:
            return Conversation.Channel.SMS
        if self.email:
            return Conversation.Channel.EMAIL
        return None

    @property
    def resolved_channel(self):
        if self.preferred_channel != self.PreferredChannel.AUTOMATIC:
            return self.preferred_channel
        return self.automatic_channel


class Conversation(models.Model):
    class Channel(models.TextChoices):
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "Email"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    contact = models.ForeignKey(
        Guest,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.SMS,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    marked_unread = models.BooleanField(default=False)
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_message_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["contact", "channel"],
                name="one_conversation_per_contact_channel",
            ),
        ]

    def __str__(self):
        return f"{self.contact} · {self.get_channel_display()}"

    @property
    def unread_count(self):
        count = self.messages.filter(
            direction=Message.Direction.INCOMING,
            is_read=False,
        ).count()
        return max(count, int(self.marked_unread))


class Message(models.Model):
    source_vendor = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="The vendor that originated this message, when known.",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="sent_messages",
    )
    sender_name = models.CharField(max_length=200, blank=True)

    class Direction(models.TextChoices):
        INCOMING = "in", "Incoming"
        OUTGOING = "out", "Outgoing"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        LOCAL = "local", "Local only"
        QUEUED = "queued", "Queued"
        ACCEPTED = "accepted", "Accepted"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        UNDELIVERED = "undelivered", "Undelivered"
        FAILED = "failed", "Failed"
        READ = "read", "Read"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    direction = models.CharField(max_length=3, choices=Direction.choices)
    body = models.TextField()
    provider_sid = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    is_read = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.get_direction_display()}: {self.body[:40]}"
