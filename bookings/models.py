import hashlib
from datetime import timezone as datetime_timezone

from django.conf import settings
from django.db import models
from django.utils import timezone

from messaging.models import Guest, Conversation, Message


def departure_fingerprint(product_id, start_time):
    """Return the canonical identity for one operating departure.

    Vendor event ids are intentionally excluded: several marketplaces may
    sell seats on the same departure.  The current operating rule is one
    departure per product and exact start instant.
    """
    if not product_id or not start_time:
        return None
    if timezone.is_naive(start_time):
        start_time = timezone.make_aware(start_time, timezone.get_default_timezone())
    instant = start_time.astimezone(datetime_timezone.utc).replace(microsecond=0)
    natural_key = f"v1:{product_id}:{instant.isoformat()}"
    return hashlib.sha256(natural_key.encode()).hexdigest()


class Guide(models.Model):
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    is_admin = models.BooleanField(default=True)

    class Meta:
        ordering = ["first_name", "last_name"]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return self.full_name


class TourProduct(models.Model):
    """A tour offering, independent of its scheduled departures."""
    name = models.CharField(max_length=240)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Tour(models.Model):
    """A scheduled departure (keeps the existing UI/model name)."""
    product = models.ForeignKey(TourProduct, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="departures")
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="assigned_tours")
    report = models.TextField(blank=True)
    name = models.CharField(max_length=240)
    start_time = models.DateTimeField()
    fingerprint = models.CharField(max_length=64, unique=True, null=True, editable=False)

    class Meta:
        ordering = ["start_time", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.fingerprint = departure_fingerprint(self.product_id, self.start_time)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and {"product", "product_id", "start_time"} & set(update_fields):
            kwargs["update_fields"] = list(set(update_fields) | {"fingerprint"})
        return super().save(*args, **kwargs)

    @property
    def people_count(self):
        guests = getattr(self, "_display_guests", None)
        if guests is None:
            guests = self.guests.all()
        return sum(g.adults + g.children for g in guests if g.attendance != "canceled")


class Booking(models.Model):
    """One guest's reservation and party on one scheduled departure."""

    @property
    def vendor_label(self):
        mapping = getattr(self, "vendorbooking", None)
        return mapping.display_vendor if mapping else ("Manual/Walk-up" if self.is_manual else "Direct")

    class Attendance(models.TextChoices):
        EXPECTED = "expected", "Booked"
        PRESENT = "present", "Checked in"
        CANCELED = "canceled", "Canceled"

    class Feedback(models.TextChoices):
        NONE = "none", "No feedback"
        UP = "up", "Thumbs up"
        DOWN = "down", "Thumbs down"

    attendance = models.CharField(max_length=16, choices=Attendance.choices, default=Attendance.EXPECTED)
    attendance_overridden = models.BooleanField(default=False)
    imported = models.BooleanField(default=False)
    adults = models.PositiveIntegerField(default=1)
    children = models.PositiveIntegerField(default=0)
    original_adults = models.PositiveIntegerField(default=1)
    original_children = models.PositiveIntegerField(default=0)
    party_size_overridden = models.BooleanField(default=False)
    feedback = models.CharField(
        max_length=8,
        choices=Feedback.choices,
        default=Feedback.NONE,
    )
    is_manual = models.BooleanField(default=False)
    tour_notes = models.TextField(blank=True)
    special_requests = models.TextField(blank=True)
    language = models.CharField(max_length=80, blank=True)
    booking_issue = models.TextField(blank=True)
    class WelcomeStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        LOCAL = "local", "Local only"
        FAILED = "failed", "Failed"
        UNAVAILABLE = "unavailable", "No contact method"

    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    email = models.EmailField(blank=True, null=True)
    contact = models.ForeignKey(
        Guest,
        on_delete=models.PROTECT,
        related_name="bookings",
    )
    booked_tour = models.ForeignKey(
        Tour,
        on_delete=models.CASCADE,
        related_name="guests",
    )
    welcome_channel = models.CharField(
        max_length=20,
        choices=Conversation.Channel.choices,
        blank=True,
    )
    welcome_status = models.CharField(
        max_length=20,
        choices=WelcomeStatus.choices,
        default=WelcomeStatus.PENDING,
    )
    welcome_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def conversation(self):
        conversations = getattr(self.contact, "bookings_conversations", None)
        if conversations is not None:
            conversations_by_channel = {
                conversation.channel: conversation
                for conversation in conversations
            }
            preferred_channel = self.contact.resolved_channel
            if preferred_channel in conversations_by_channel and self._channel_available(
                preferred_channel
            ):
                return conversations_by_channel[preferred_channel]
            return next(
                (
                    conversation
                    for conversation in conversations
                    if self._channel_available(conversation.channel)
                ),
                None,
            )

        preferred_channel = self.contact.resolved_channel
        if preferred_channel and self._channel_available(preferred_channel):
            preferred = self.contact.conversations.filter(
                channel=preferred_channel,
            ).first()
            if preferred:
                return preferred
        return next(
            (
                conversation
                for conversation in self.contact.conversations.all()
                if self._channel_available(conversation.channel)
            ),
            None,
        )

    @property
    def can_greet(self):
        return (self.attendance != self.Attendance.CANCELED
                and self.contact.greeting_eligible
                and bool(self.contact.phone_number or self.contact.email))

    @property
    def greeting_unavailable_reason(self):
        if self.contact.greeted_at:
            return "This guest has already been greeted."
        if self.contact.has_attended:
            return "Returning guest — no greeting needed."
        if self.attendance == self.Attendance.CANCELED:
            return "Guest is canceled"
        return "Add a phone number or email address to send the welcome message"

    @property
    def history_status(self):
        return self.get_attendance_display()

    def _channel_available(self, channel):
        if channel in {
            Conversation.Channel.SMS,
            Conversation.Channel.WHATSAPP,
        }:
            return bool(self.contact.phone_number)
        return channel == Conversation.Channel.EMAIL and bool(self.contact.email)

    @property
    def unread_count(self):
        if hasattr(self, "_unread_count"):
            return self._unread_count
        count = Message.objects.filter(
            conversation__contact_id=self.contact_id,
            direction=Message.Direction.INCOMING,
            is_read=False,
        ).count()
        return max(count, int(self.contact.conversations.filter(marked_unread=True).exists()))

    @property
    def chat_started(self):
        """A contact's chat is started once any channel has a message."""
        vendor_booking = getattr(self, "vendorbooking", None)
        if vendor_booking is not None:
            if vendor_booking.conversation_started_at:
                return True
        if hasattr(self, "_has_messages"):
            return self._has_messages
        return self.contact.conversations.filter(messages__isnull=False).exists()

    def __str__(self):
        return self.full_name
