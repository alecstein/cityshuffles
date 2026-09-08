from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from bookings.models import TourProduct


class CatalogTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="catalog-admin",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_staff_can_create_and_view_official_tour(self):
        response = self.client.post(
            reverse("catalog:create"),
            {"name": "Brooklyn Walk", "description": "Bridges and neighborhoods."},
        )
        self.assertRedirects(response, reverse("catalog:index"))
        product = TourProduct.objects.get()
        self.assertEqual(product.description, "Bridges and neighborhoods.")
        self.assertContains(self.client.get(reverse("catalog:index")), "Brooklyn Walk")

    def test_staff_can_edit_official_tour(self):
        product = TourProduct.objects.create(name="Old name")
        response = self.client.post(
            reverse("catalog:edit", args=[product.pk]),
            {"name": "New name", "description": "Updated description."},
        )
        self.assertRedirects(response, reverse("catalog:index"))
        product.refresh_from_db()
        self.assertEqual((product.name, product.description), ("New name", "Updated description."))

    def test_non_staff_cannot_open_catalog(self):
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])
        response = self.client.get(reverse("catalog:index"))
        self.assertEqual(response.status_code, 302)
