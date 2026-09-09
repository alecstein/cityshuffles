from django.urls import path
from . import views

app_name = "messaging"

urlpatterns = [
    path("unread/", views.unread_badge, name="unread_badge"),
    path("", views.inbox, name="inbox"),
    path("conversations/", views.conversation_list, name="conversation_list"),
    path("conversations/<int:pk>/interaction/", views.interaction, name="interaction"),
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
