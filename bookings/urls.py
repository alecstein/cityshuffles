from django.urls import path
from . import views

app_name = "bookings"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.add_booking, name="new_booking"),
    path("tours/<int:tour_pk>/add-booking/", views.add_booking, name="add_booking"),
    path("new/<int:pk>/start-conversation/", views.start_booking_conversation, name="start_booking_conversation"),
    path("new/<int:pk>/open-conversation/", views.open_booking_conversation, name="open_booking_conversation"),
    path("new/<int:pk>/ignore/", views.ignore_new_booking, name="ignore_new_booking"),
    path("guests/<int:pk>/start-conversation/", views.start_guest_conversation, name="start_guest_conversation"),
    path("guests/<int:pk>/open-conversation/", views.open_guest_conversation, name="open_guest_conversation"),
    path("guests/<int:pk>/update/", views.guest_update, name="guest_update"),
    path("guests/<int:pk>/attendance/", views.guest_attendance_update, name="guest_attendance_update"),
    path("guests/<int:pk>/delete/", views.guest_delete, name="guest_delete"),
]
