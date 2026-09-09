"""Durable, deduplicated thank-you sends with per-recipient delivery tracking."""
import re
import threading
from datetime import timedelta

from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone
from twilio.base.exceptions import TwilioRestException

from bookings.models import Booking
from message_templates.models import MessageTemplate
from messaging.models import Conversation, Message
from messaging.services import SendResult, send_message
from .models import ThankYouAction, ThankYouDelivery


DEFAULT_THANK_YOU = "Thanks, {guest_name}! We hope you enjoyed {tour_name} with {guide_first_name}."


def render_thank_you(body, guest, tour, photos_url=""):
    guide = (tour.responsible.first_name or tour.responsible.username) if tour.responsible else "your guide"
    values = {
        "guest_name": guest.full_name,
        "guest_first_name": guest.first_name,
        "tour_name": tour.name, "tour": tour.name,
        "guide_first_name": guide, "guide": guide,
        "booking_time": timezone.localtime(tour.start_time).strftime("%b %-d, %-I:%M %p"),
    }
    if photos_url:
        values["photos_url"] = photos_url
    rendered = MessageTemplate(body=body).render(values)
    if re.search(r"\{[^{}]+\}", rendered):
        raise ValueError("This template contains a placeholder that cannot be filled. Please edit it in Templates first.")
    return rendered


@transaction.atomic
def request_thank_you(tour, sender, template=None, *, kind="closing", body=None, request_key=None, photos_url=""):
    if kind not in {"closing", "custom", "photos"}:
        raise ValueError("Photos will be available when photo uploads are ready.")
    if kind == "custom" and not request_key:
        raise ValueError("Please reload the page before sending this message.")
    existing = (ThankYouAction.objects.filter(tour=tour, request_key=request_key).first() if kind == "custom"
                else ThankYouAction.objects.filter(tour=tour, kind=kind).first())
    if existing:
        return existing, False
    guests = list(tour.guests.exclude(attendance=Booking.Attendance.CANCELED)
                  .select_related("contact", "vendorbooking__connection").order_by("pk"))
    if not guests:
        raise ValueError("There are no active bookings on this tour.")
    if kind == "photos":
        from django.conf import settings
        from urllib.parse import urlsplit
        from photos.models import Album
        album = Album.objects.filter(tour=tour).first()
        if not album or not album.photos.exists() or not photos_url:
            raise ValueError("Upload photos before sending the gallery link.")
        real_guests = any(not (getattr(g, "vendorbooking", None) and (g.vendorbooking.is_mock or g.vendorbooking.connection.vendor == "demotours")) for g in guests)
        if real_guests and (not settings.PUBLIC_BASE_URL or urlsplit(settings.PUBLIC_BASE_URL).scheme != "https"):
            raise ValueError("Set a public HTTPS site address before sending photo links to real guests. Local galleries can be previewed here.")
    body = body if kind == "custom" else (template.body if template else DEFAULT_THANK_YOU)
    if not (body or "").strip():
        raise ValueError("Write a message before sending.")
    if len(body) > 10000:
        raise ValueError("Please keep group messages under 10,000 characters.")
    # Validate every personalized message before recording or dispatching anything.
    recipients = {}
    for guest in guests:
        if guest.contact_id not in recipients:
            rendered = render_thank_you(body, guest, tour, photos_url)
            recipients[guest.contact_id] = (guest, rendered, rendered)
    identity = {"tour": tour, "request_key": request_key} if kind == "custom" else {"tour": tour, "kind": kind}
    action, created = ThankYouAction.objects.get_or_create(**identity, defaults={
        "template_name": "Custom message" if kind == "custom" else (template.name if template else "Closing message"),
        "template_body": body, "requested_by": sender, **({"kind": kind} if kind == "custom" else {}),
    })
    if not created:
        return action, False
    for guest, rendered, email_rendered in recipients.values():
        vendor = getattr(guest, "vendorbooking", None)
        ThankYouDelivery.objects.create(
            action=action, contact=guest.contact, guest=guest, guest_name=guest.full_name,
            body=rendered, email_body=email_rendered, is_demo=bool(vendor and (vendor.is_mock or vendor.connection.vendor == "demotours")),
        )
    transaction.on_commit(kick_delivery_worker)
    return action, True


def delivery_channel(contact):
    latest = (Message.objects.filter(conversation__contact=contact, direction=Message.Direction.INCOMING)
              .order_by("-created_at", "-pk").values_list("conversation__channel", flat=True).first())
    candidates = [latest or contact.last_inbound_channel, contact.resolved_channel,
                  Conversation.Channel.EMAIL if contact.email else None,
                  Conversation.Channel.SMS if contact.phone_number else None]
    for channel in candidates:
        if channel == Conversation.Channel.EMAIL and contact.email:
            return channel
        if channel in {Conversation.Channel.SMS, Conversation.Channel.WHATSAPP} and contact.phone_number:
            return channel
    return None


def send_delivery(pk):
    # Atomic claim prevents double clicks, multiple workers and retries racing.
    if not ThankYouDelivery.objects.filter(pk=pk, state="pending").update(state="sending", attempted_at=timezone.now()):
        return
    delivery = ThankYouDelivery.objects.select_related("contact", "action__requested_by").get(pk=pk)
    channel = delivery_channel(delivery.contact)
    if not channel:
        ThankYouDelivery.objects.filter(pk=pk).update(state="blocked", detail="Not sent — no contact information")
        return
    body = delivery.email_body if channel == "email" and delivery.email_body is not None else delivery.body
    if not body.strip():
        ThankYouDelivery.objects.filter(pk=pk).update(state="blocked", detail="Not sent — no template for this channel")
        return
    conversation, _ = Conversation.objects.get_or_create(contact=delivery.contact, channel=channel)
    sender = delivery.action.requested_by
    message = Message.objects.create(
        conversation=conversation, direction=Message.Direction.OUTGOING,
        body=body, status=Message.Status.SENDING, is_read=True,
        sent_by=sender, sender_name=(sender.get_full_name() or sender.username) if sender else "",
        source_vendor="demotours" if delivery.is_demo else "",
    )
    ThankYouDelivery.objects.filter(pk=pk).update(message=message)
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=message.created_at)
    state = "complete"
    try:
        result = SendResult(None, Message.Status.LOCAL) if delivery.is_demo else send_message(conversation, body)
        message.provider_sid, message.status = result.provider_sid, result.status
    except TwilioRestException as exc:
        # A timeout/5xx can mean the provider accepted the send. Never automatically resend it.
        if exc.status and 400 <= exc.status < 500:
            message.status = Message.Status.FAILED
            message.error_message = f"Twilio rejected this message (code {exc.code or exc.status})."
        else:
            state = "review"
            message.error_message = "Delivery could not be confirmed. Check the provider before sending again."
    except Exception:
        state = "review"
        message.error_message = "Delivery could not be confirmed. Check the provider before sending again."
    message.save(update_fields=["provider_sid", "status", "error_message"])
    ThankYouDelivery.objects.filter(pk=pk).update(state=state, detail=message.error_message[:300])


def process_pending_deliveries():
    ThankYouDelivery.objects.filter(state="sending", attempted_at__lt=timezone.now() - timedelta(minutes=10)).update(
        state="review", detail="Sending was interrupted. Check the provider before sending again.",
    )
    for pk in list(ThankYouDelivery.objects.filter(state="pending").values_list("pk", flat=True)):
        try:
            send_delivery(pk)
        except Exception:
            # Keep processing other recipients if one record fails locally.
            ThankYouDelivery.objects.filter(pk=pk, state="sending").update(
                state="review", detail="Could not confirm this delivery. Check the chat before sending again.",
            )


_worker_lock = threading.Lock()


def kick_delivery_worker():
    """Dispatch promptly; persisted pending work is also resumed by the integration worker."""
    def run():
        if not _worker_lock.acquire(blocking=False):
            return
        try:
            close_old_connections()
            process_pending_deliveries()
        finally:
            close_old_connections()
            _worker_lock.release()
    threading.Thread(target=run, name="thank-you-delivery", daemon=True).start()


def retry_delivery(delivery):
    delivery = ThankYouDelivery.objects.select_related("message").get(pk=delivery.pk)
    if not delivery.can_retry:
        return False
    eligible = Q(state="blocked") | Q(state="complete", is_demo=False, message__status__in=["failed", "undelivered", "local"])
    changed = ThankYouDelivery.objects.filter(eligible, pk=delivery.pk).update(state="pending", detail="")
    if changed:
        transaction.on_commit(kick_delivery_worker)
    return bool(changed)
