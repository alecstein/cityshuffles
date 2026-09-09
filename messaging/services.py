from dataclasses import dataclass

from django.conf import settings
from twilio.rest import Client

from .models import Conversation, Message


@dataclass
class SendResult:
    provider_sid: str | None
    status: str


def _sender_for(channel):
    if channel == Conversation.Channel.WHATSAPP:
        return settings.TWILIO_WHATSAPP_FROM
    return settings.TWILIO_SMS_FROM


def twilio_is_configured(channel):
    return bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_AUTH_TOKEN
        and _sender_for(channel)
    )


def _address(channel, number):
    if channel == Conversation.Channel.WHATSAPP:
        return f"whatsapp:{number}"
    return number


def send_message(conversation, body):
    """
    Send via Twilio when credentials are configured.

    With no Twilio credentials, return LOCAL so the whole UI remains
    usable during development.
    """
    if not conversation.contact.bookings.exists():
        raise ValueError("A guest needs a current or past booking before messaging.")
    if conversation.channel == Conversation.Channel.EMAIL:
        from integrations.gmail import send_email
        from integrations.models import Connection

        gmail = Connection.objects.filter(vendor="gmail", enabled=True, auth_status="ok").first()
        if gmail and gmail.account_id:
            provider_sid = send_email(gmail, conversation.contact.email, body)
            return SendResult(provider_sid=provider_sid, status=Message.Status.SENT)
        return SendResult(
            provider_sid=None,
            status=Message.Status.LOCAL,
        )

    if not twilio_is_configured(conversation.channel):
        return SendResult(
            provider_sid=None,
            status=Message.Status.LOCAL,
        )

    client = Client(
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_AUTH_TOKEN,
    )

    kwargs = {
        "body": body,
        "from_": _address(
            conversation.channel,
            _sender_for(conversation.channel),
        ),
        "to": _address(
            conversation.channel,
            conversation.contact.phone_number,
        ),
    }

    if settings.TWILIO_STATUS_CALLBACK_URL:
        kwargs["status_callback"] = settings.TWILIO_STATUS_CALLBACK_URL

    result = client.messages.create(**kwargs)

    known_statuses = {value for value, _label in Message.Status.choices}
    status = result.status if result.status in known_statuses else Message.Status.QUEUED

    return SendResult(
        provider_sid=result.sid,
        status=status,
    )
