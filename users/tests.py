from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class UserManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", is_staff=True)
        self.guide = User.objects.create_user(username="guide")

    def test_guide_can_change_names_but_not_role(self):
        self.client.force_login(self.guide)
        self.client.post(reverse("users:profile"), {"first_name": "Alec", "last_name": "Stein", "email": "alec@example.com", "is_staff": "on"})
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.get_full_name(), "Alec Stein")
        self.assertFalse(self.guide.is_staff)
        for url in [reverse("users:index"), reverse("users:create"), reverse("users:edit", args=[self.admin.pk])]:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_admin_creates_user_with_hashed_validated_password(self):
        self.client.force_login(self.admin)
        data = {"username": "newguide", "first_name": "New", "last_name": "Guide", "email": "guide@example.com", "password1": "Long-Random-Phrase!923", "password2": "Long-Random-Phrase!923", "is_staff": "on"}
        self.assertEqual(self.client.post(reverse("users:create"), data).status_code, 302)
        user = User.objects.get(username="newguide")
        self.assertTrue(user.check_password(data["password1"]))
        self.assertTrue(user.is_staff)
        data.update(username="badpassword", password1="123", password2="123")
        self.client.post(reverse("users:create"), data)
        self.assertFalse(User.objects.filter(username="badpassword").exists())

    def test_admin_cannot_demote_self_or_edit_superuser(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("users:edit", args=[self.admin.pk]), {"username": "admin"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)
        self.assertTrue(self.admin.is_active)
        root = User.objects.create_superuser(username="root", email="root@example.com", password="testing-root!923")
        self.assertEqual(self.client.post(reverse("users:edit", args=[root.pk]), {"username": "root"}).status_code, 403)

    def test_home_redirects_dashboard(self):
        self.client.force_login(self.guide)
        self.assertRedirects(self.client.get("/"), reverse("bookings:index"))
