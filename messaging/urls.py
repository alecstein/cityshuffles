from django.urls import path
from . import views

app_name = "messaging"

urlpatterns = [
    path("unread/", views.unread_badge, name="unread_badge"),
    path("conversations/<int:pk>/contact/", views.edit_contact, name="edit_contact"),
    path("", views.inbox, name="inbox"),
    path("conversations/", views.conversation_list, name="conversation_list"),
    path("conversations/new/", views.new_conversation, name="new_conversation"),
    path("conversations/<int:pk>/", views.conversation, name="conversation"),
    path("conversations/<int:pk>/messages/", views.message_list, name="message_list"),
    path("conversations/<int:pk>/send/", views.send, name="send"),
    path(
        "contacts/<int:contact_pk>/channels/<str:channel>/",
        views.open_channel,
        name="open_channel",
    ),
    path("twilio/inbound/", views.twilio_inbound, name="twilio_inbound"),
    path("twilio/status/", views.twilio_status, name="twilio_status"),
]
