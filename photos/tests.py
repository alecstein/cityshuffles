import io
import tempfile
import uuid
from unittest.mock import patch
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from bookings.models import Tour, Booking
from messaging.models import Guest
from message_templates.models import MessageTemplate
from mytours.thank_you import request_thank_you
from .models import Photo, Album


class PhotoTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.directory.name)
        self.settings_override.enable()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_user(username="guide")
        self.tour = Tour.objects.create(name="Photo tour", start_time=timezone.now(), responsible=self.user)
        self.client.force_login(self.user)
        output = io.BytesIO()
        Image.new("RGB", (1200, 800), "green").save(output, "JPEG")
        self.original = output.getvalue()

    def upload(self, upload_id=None):
        return self.client.post(reverse("photos:upload", args=[self.tour.pk]), {"photo": SimpleUploadedFile("original.jpg", self.original, content_type="image/jpeg"), "upload_id": upload_id or str(uuid.uuid4())})

    def test_original_preserved_and_guest_gallery_available(self):
        self.assertEqual(self.upload().status_code, 200)
        photo = Photo.objects.get()
        with photo.original.open("rb") as source:
            self.assertEqual(source.read(), self.original)
        with photo.display.open("rb") as source:
            self.assertEqual(Image.open(source).size, (1200, 800))
        with photo.thumbnail.open("rb") as source:
            self.assertEqual(Image.open(source).size, (600, 400))
        self.client.logout()
        page = self.client.get(reverse("photos:album", args=[photo.album.token]))
        self.assertContains(page, "Thanks for joining us!")
        self.assertContains(page, "Download original")
        self.assertEqual(page["X-Robots-Tag"], "noindex, nofollow, noarchive")
        self.assertEqual(self.client.get(reverse("photos:asset", args=[uuid.uuid4(), photo.pk, "original"])).status_code, 404)

    def test_idempotent_retry_and_permissions(self):
        upload_id = str(uuid.uuid4())
        self.upload(upload_id)
        self.upload(upload_id)
        self.assertEqual(Photo.objects.count(), 1)
        other = get_user_model().objects.create_user(username="other")
        self.client.force_login(other)
        self.assertEqual(self.upload().status_code, 404)
        self.assertEqual(self.client.post(reverse("photos:remove", args=[Photo.objects.get().pk])).status_code, 404)

    def test_invalid_file_rejected(self):
        self.original = b"<script>not an image</script>"
        self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(Photo.objects.count(), 0)

    def test_delete_removes_files(self):
        self.upload()
        photo = Photo.objects.get()
        storage, name = photo.original.storage, photo.original.name
        self.assertEqual(self.client.post(reverse("photos:remove", args=[photo.pk])).status_code, 200)
        self.assertFalse(storage.exists(name))

    @patch("mytours.thank_you.kick_delivery_worker")
    @override_settings(PUBLIC_BASE_URL="https://example.com")
    def test_photos_message_snapshots_departure_link(self, worker):
        self.upload()
        guest = Booking.objects.create(first_name="Guest", booked_tour=self.tour, contact=Guest.objects.create(name="Guest"), imported=True)
        template = MessageTemplate.objects.get(system_key="photos")
        url = "https://example.com" + reverse("photos:album", args=[Album.objects.get().token])
        action, created = request_thank_you(self.tour, self.user, template, kind="photos", photos_url=url)
        self.assertTrue(created)
        self.assertIn(url, action.deliveries.get().body)
        repeated, created = request_thank_you(self.tour, self.user, template, kind="photos", photos_url=url)
        self.assertFalse(created)
        self.assertEqual(action.pk, repeated.pk)

    @override_settings(PUBLIC_BASE_URL="")
    def test_local_link_not_sent_to_real_guests(self):
        self.upload()
        Booking.objects.create(first_name="Guest", booked_tour=self.tour, contact=Guest.objects.create(name="Guest"), imported=True)
        with self.assertRaisesMessage(ValueError, "public HTTPS"):
            request_thank_you(self.tour, self.user, MessageTemplate.objects.get(system_key="photos"), kind="photos", photos_url="http://localhost/photos/test/")
