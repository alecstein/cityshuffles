from django.urls import path
from . import views

app_name = "integrations"
urlpatterns = [path("", views.index, name="index"),
               path("sync/", views.manual_sync, name="sync"),
               path("cancel/", views.cancel_sync, name="cancel"),
               path("sync/status/", views.sync_status, name="sync_status"),
               path("status/", views.sync_status, name="status"),
               path("freetour/connect/", views.connect_freetour, name="connect_freetour"),
               path("freetour/sync/", views.sync_freetour, name="sync_freetour"),
               path("freetour/cancel/", views.cancel_freetour_sync, name="cancel_freetour_sync"),
               path("tours/map/", views.map_vendor_tour, name="map_vendor_tour"),
               path("gmail/", views.connect_gmail, name="connect_gmail"),
               path("gmail/callback/", views.gmail_callback, name="gmail_callback"),
               path("gmail/disconnect/", views.disconnect_gmail, name="disconnect_gmail")]
