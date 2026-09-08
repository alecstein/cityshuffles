from django.db import transaction
from django.db.models import Count, Exists, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from messaging.models import Conversation, Message
from messaging.services import send_message

from .models import Guest


WELCOME_MESSAGE = (
    "Welcome to CityShuffles! We’re looking forward to having you on the tour."
)


def with_chat_state(queryset):
    """Add contact-wide message state without per-guest queries."""
    contact_messages = Message.objects.filter(
        conversation__contact_id=OuterRef("contact_id"),
    )
    unread_counts = (
        contact_messages
        .filter(direction=Message.Direction.INCOMING, is_read=False)
        .values("conversation__contact_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    return queryset.annotate(
        _has_messages=Exists(contact_messages),
        _unread_count=Coalesce(
            Subquery(unread_counts, output_field=IntegerField()),
            0,
        ),
    )


def choose_welcome_channel(guest):
    contact = guest.contact

    if (
        contact.preferred_channel == contact.PreferredChannel.WHATSAPP
        and contact.phone_number
        and contact.whatsapp_opted_in
    ):
        return Conversation.Channel.WHATSAPP

    if contact.last_inbound_channel == Conversation.Channel.WHATSAPP and contact.phone_number:
        return Conversation.Channel.WHATSAPP

    if contact.phone_number:
        return Conversation.Channel.SMS

    if contact.email:
        return Conversation.Channel.EMAIL

    return None


def send_welcome_for_guest(guest_id, *, force=False, sender=None, source_vendor=""):
    guest = Guest.objects.select_related("contact").get(pk=guest_id)
    if guest.imported and not force:
        return  # Importing real reservations must never send unsolicited welcomes.
    if guest.welcome_sent_at:
        return True

    channel = choose_welcome_channel(guest)
    if not channel:
        Guest.objects.filter(pk=guest.pk).update(
            welcome_status=Guest.WelcomeStatus.UNAVAILABLE,
        )
        return False

    conversation, _ = Conversation.objects.get_or_create(
        contact=guest.contact,
        channel=channel,
    )

    from message_templates.models import MessageTemplate
    from mytours.thank_you import render_thank_you
    template = MessageTemplate.objects.filter(system_key="opening").first()
    body = template.body if template else WELCOME_MESSAGE
    if not body.strip():
        Guest.objects.filter(pk=guest.pk).update(welcome_status=Guest.WelcomeStatus.UNAVAILABLE)
        return False
    try:
        body = render_thank_you(body, guest, guest.booked_tour)
        mapping = getattr(guest, "vendorbooking", None)
        if mapping and mapping.is_mock:
            from messaging.services import SendResult
            result = SendResult(provider_sid=None, status=Message.Status.LOCAL)
        else:
            result = send_message(conversation, body)
        message = Message.objects.create(
            conversation=conversation,
            direction=Message.Direction.OUTGOING,
            body=body,
            provider_sid=result.provider_sid,
            status=result.status,
            is_read=True,
            sent_by=sender,
            sender_name=(
                sender.get_full_name() or sender.get_username()
                if sender else ""
            ),
            source_vendor=source_vendor,
        )
        conversation.last_message_at = message.created_at
        conversation.save(update_fields=["last_message_at"])
        welcome_status = (
            Guest.WelcomeStatus.LOCAL
            if result.status == Message.Status.LOCAL
            else Guest.WelcomeStatus.SENT
        )
    except Exception:
        welcome_status = Guest.WelcomeStatus.FAILED

    Guest.objects.filter(pk=guest.pk).update(
        welcome_channel=channel,
        welcome_status=welcome_status,
        welcome_sent_at=(
            timezone.now()
            if welcome_status != Guest.WelcomeStatus.FAILED
            else None
        ),
    )
    return welcome_status != Guest.WelcomeStatus.FAILED


def start_conversation_for_booking(vendor_booking, sender=None):
    """Explicitly start a vendor booking's conversation and send its welcome."""
    guest = Guest.objects.select_related("contact").get(pk=vendor_booking.booking_id)
    conversation = start_conversation_for_guest(
        guest,
        sender=sender,
        source_vendor=vendor_booking.connection.vendor,
    )
    if conversation is None:
        return None

    vendor_booking.is_new = False
    vendor_booking.conversation_started_at = timezone.now()
    vendor_booking.save(update_fields=["is_new", "conversation_started_at"])
    return conversation


def open_conversation_for_booking(vendor_booking):
    """Open a booking's chat without sending the automatic welcome message."""
    guest = Guest.objects.select_related("contact").get(pk=vendor_booking.booking_id)
    return open_conversation_for_guest(guest)


def open_conversation_for_guest(guest):
    """Open a guest's chat without sending the automatic welcome message."""
    guest = Guest.objects.select_related("contact").get(pk=guest.pk)
    channel = choose_welcome_channel(guest)
    if not channel:
        return None

    conversation, _created = Conversation.objects.get_or_create(
        contact=guest.contact,
        channel=channel,
    )
    return conversation


def start_conversation_for_guest(guest, sender=None, source_vendor=""):
    """Start a guest's first chat, or return its existing default channel."""
    success = send_welcome_for_guest(
        guest.pk,
        force=True,
        sender=sender,
        source_vendor=source_vendor,
    )
    if not success:
        return None

    guest.refresh_from_db(fields=["welcome_channel"])
    channel = guest.welcome_channel or choose_welcome_channel(guest)
    if not channel:
        return None
    return Conversation.objects.get(contact=guest.contact, channel=channel)


def schedule_welcome_for_guest(guest):
    transaction.on_commit(lambda: send_welcome_for_guest(guest.pk))
