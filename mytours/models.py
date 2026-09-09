from django.conf import settings
from django.db import models
import re


class ThankYouAction(models.Model):
    """A group-message batch; retained model name preserves existing delivery history."""

    tour = models.ForeignKey("bookings.Tour", on_delete=models.CASCADE, related_name="group_messages")
    kind = models.CharField(max_length=20, default="closing")
    request_key = models.UUIDField(null=True, blank=True, unique=True)
    template_name = models.CharField(max_length=160)
    template_body = models.TextField()
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tour", "kind"], condition=models.Q(kind__in=["closing", "photos"]), name="one_quick_message_per_tour")]

    @property
    def successfully_sent(self):
        deliveries = list(self.deliveries.all())
        return bool(deliveries) and all(d.state == "complete" and d.message and d.message.status in {"sent", "delivered", "read"} for d in deliveries)


class ThankYouDelivery(models.Model):
    action = models.ForeignKey(ThankYouAction, on_delete=models.CASCADE, related_name="deliveries")
    contact = models.ForeignKey("messaging.Guest", on_delete=models.PROTECT)
    guest = models.ForeignKey("bookings.Booking", null=True, on_delete=models.SET_NULL)
    guest_name = models.CharField(max_length=200)
    body = models.TextField()
    email_body = models.TextField(null=True, blank=True)
    is_demo = models.BooleanField(default=False)
    state = models.CharField(max_length=20, default="pending")
    message = models.ForeignKey("messaging.Message", null=True, blank=True, on_delete=models.SET_NULL)
    detail = models.CharField(max_length=300, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["action", "contact"], name="one_thank_you_per_tour_contact")]
        ordering = ["pk"]

    @property
    def status_label(self):
        if self.state == "pending":
            return "Waiting to send"
        if self.state == "sending":
            return "Sending"
        if self.state == "review":
            return "Delivery unconfirmed — check chat"
        if self.state == "blocked":
            return self.detail
        if self.message:
            if self.message.status == "local":
                return "Demo — saved locally" if self.is_demo else "Not sent — channel not connected"
            return self.message.get_status_display()
        return "Delivery record unavailable"

    @property
    def row_color(self):
        if self.state == "complete" and self.message and self.message.status in {"delivered", "read"}:
            return "delivered"
        if self.state == "blocked" or (self.message and self.message.status in {"failed", "undelivered"}) or (self.state == "complete" and self.message and self.message.status == "local" and not self.is_demo):
            return "failed"
        return "pending"

    @property
    def can_retry(self):
        if self.message and re.search(r"\b(21610|30007)\b", self.message.error_message or ""):
            # Opt-out / carrier filtering require resolution, not another send.
            return False
        return self.state == "blocked" or (
            self.state == "complete" and self.message is not None
            and self.message.status in {"failed", "undelivered", "local"}
            and not self.is_demo
        )
