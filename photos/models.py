import uuid
from django.conf import settings
from django.db import models


class Album(models.Model):
    tour = models.OneToOneField("bookings.Tour", on_delete=models.CASCADE, related_name="photo_album")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)


class Photo(models.Model):
    album = models.ForeignKey(Album, on_delete=models.CASCADE, related_name="photos")
    upload_id = models.UUIDField(default=uuid.uuid4)
    original = models.FileField(upload_to="tour-photos/originals/")
    display = models.FileField(upload_to="tour-photos/display/")
    thumbnail = models.FileField(upload_to="tour-photos/thumbnails/")
    filename = models.CharField(max_length=240)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["album", "upload_id"], name="unique_album_upload")]
