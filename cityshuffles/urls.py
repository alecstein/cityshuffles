from django.contrib import admin
from django.urls import include, path
from messaging import push_views

urlpatterns = [
    path("photos/", include("photos.urls")),
    path("push-worker.js", push_views.service_worker),
    path("manifest.webmanifest", push_views.manifest),
    path("notifications/config/", push_views.config),
    path("notifications/device/", push_views.device),
    path("notifications/unread/", push_views.unread),
    path("users/", include("users.urls")),
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("core.urls")),
    path("messages/", include("messaging.urls")),
    path("bookings/", include("bookings.urls")),
    path("my-tours/", include("mytours.urls")),
    path("integrations/", include("integrations.urls")),
    path("templates/", include("message_templates.urls")),
]
