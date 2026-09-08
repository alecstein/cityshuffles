from datetime import timedelta
from django.db import models
from django.utils import timezone


class Connection(models.Model):
    vendor = models.CharField(max_length=32, unique=True, default="guruwalk")
    account_id = models.CharField(max_length=200, blank=True)
    enabled = models.BooleanField(default=False)
    auth_status = models.CharField(max_length=20, default="unknown")
    auth_checked_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=20, default="never")
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    worker_seen_at = models.DateTimeField(null=True, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    imported_count = models.PositiveIntegerField(default=0)
    today_last_attempt_at = models.DateTimeField(null=True, blank=True)
    today_last_sync_at = models.DateTimeField(null=True, blank=True)
    tomorrow_last_attempt_at = models.DateTimeField(null=True, blank=True)
    tomorrow_last_sync_at = models.DateTimeField(null=True, blank=True)
    future_last_attempt_at = models.DateTimeField(null=True, blank=True)
    future_last_sync_at = models.DateTimeField(null=True, blank=True)
    # A run id makes the file based worker state unambiguous when a worker
    # exits and a later run starts before the old process has fully unwound.
    sync_run_id = models.CharField(max_length=64, blank=True, default="")
    sync_reserved_at = models.DateTimeField(null=True, blank=True)

    @property
    def display_name(self):
        return {
            "guruwalk": "GuruWalk",
            "freetour": "FreeTour",
            "demotours": "DemoTours Inc.",
            "gmail": "Gmail",
            "manual": "Manual/Walk-up",
        }.get(self.vendor, self.vendor.title())

    @property
    def worker_online(self):
        return bool(self.worker_seen_at and self.worker_seen_at > timezone.now() - timedelta(minutes=2))

    @property
    def auth_current(self):
        if self.vendor == "demotours":
            return bool(self.enabled and self.auth_status == "ok")
        # Freshness decides when to recheck; it does not turn the last known
        # successful authentication into a rejected credential.
        if self.vendor in {"guruwalk", "freetour"}:
            return bool(self.enabled and self.auth_status == "ok")
        return bool(self.enabled and self.auth_status == "ok" and self.auth_checked_at
                    and self.auth_checked_at > timezone.now() - timedelta(minutes=12))

    @property
    def auth_fresh(self):
        return bool(self.auth_current and self.auth_checked_at
                    and self.auth_checked_at > timezone.now() - timedelta(minutes=12))


class VendorTour(models.Model):
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    external_id = models.CharField(max_length=100)
    name = models.CharField(max_length=240, blank=True)
    product = models.ForeignKey("bookings.TourProduct", null=True, blank=True,
                                on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["connection", "external_id"], name="vendor_tour_identity")]


class VendorEvent(models.Model):
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    external_id = models.CharField(max_length=100)
    departure = models.ForeignKey("bookings.Tour", on_delete=models.PROTECT,
                                  related_name="vendor_events")
    source = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["connection", "external_id"], name="vendor_event_identity")]


class VendorGuest(models.Model):
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    external_id = models.CharField(max_length=200)
    contact = models.ForeignKey("messaging.Contact", on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["connection", "external_id"], name="vendor_guest_identity")]


class VendorBooking(models.Model):
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE)
    external_id = models.CharField(max_length=100)
    booking = models.OneToOneField("bookings.Guest", on_delete=models.PROTECT)
    event = models.ForeignKey("integrations.VendorEvent", null=True, blank=True,
                              on_delete=models.PROTECT, related_name="bookings")
    is_new = models.BooleanField(default=True)
    is_mock = models.BooleanField(default=False)
    conversation_started_at = models.DateTimeField(null=True, blank=True)
    source = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["connection", "external_id"], name="vendor_booking_identity")]

    @property
    def display_vendor(self):
        return "DemoTours" if self.is_mock else self.connection.display_name
