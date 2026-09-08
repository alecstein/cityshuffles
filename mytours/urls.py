from django.urls import path
from . import views

app_name = "mytours"
urlpatterns = [
    path("all/", views.index, {"all_tours": True}, name="all"),
    path("tours/<int:tour_pk>/group-messages/", views.send_group_message, name="send_group_message"),
    path("tours/<int:tour_pk>/group-messages/history/", views.group_history, name="group_history"),
    path("", views.index, name="index"),
    path("tours/<int:tour_pk>/add-booking/", views.add_booking, name="add_booking"),
    path("tours/<int:tour_pk>/thank-you/", views.send_thank_you, name="send_thank_you"),
    path("tours/<int:tour_pk>/thank-you/status/", views.thank_you_status, name="thank_you_status"),
    path("thank-you/<int:pk>/retry/", views.retry_thank_you, name="retry_thank_you"),
    path("guests/<int:pk>/", views.guest_update, name="guest_update"),
    path("guests/<int:pk>/party/", views.guest_party_update, name="guest_party_update"),
    path("guests/<int:pk>/feedback/", views.guest_feedback, name="guest_feedback"),
]
