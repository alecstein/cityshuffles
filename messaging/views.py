from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import (
    Case, Count, Exists, IntegerField, Max, OuterRef, Prefetch, Q, Subquery,
    Value, When,
)
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from twilio.request_validator import RequestValidator

from .forms import MessageForm
from .models import Guest, Conversation, Message
from .services import send_message
from message_templates.models import MessageTemplate
from mytours.forms import BookingForm
from mytours.services import booking_modal_data


@login_required
@require_GET
def unread_badge(request):
    return render(request, "messaging/partials/nav_unread.html")


def _conversation_queryset(search=""):
    from bookings.models import Booking
    queryset = Conversation.objects.select_related("contact").filter(
        Exists(Booking.objects.filter(contact_id=OuterRef("contact_id")))
    )
    if search:
        matching_messages = Message.objects.filter(
            conversation=OuterRef("pk"),
            body__icontains=search,
        )
        queryset = queryset.annotate(
            search_message_match=Exists(matching_messages),
        ).filter(
            Q(contact__name__icontains=search) | Q(search_message_match=True)
        )
    latest_message = Message.objects.filter(
        conversation=OuterRef("pk"),
    ).order_by("-created_at", "-pk")
    return (
        queryset
        .annotate(
            latest_message_at=Max("messages__created_at"),
            latest_message_body=Subquery(latest_message.values("body")[:1]),
            unread_total=Count(
                "messages",
                filter=Q(
                    messages__direction=Message.Direction.INCOMING,
                    messages__is_read=False,
                ),
            ),
        )
        .order_by(
            Case(
                When(latest_message_at__isnull=False, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            "-latest_message_at",
            "-created_at",
            "-pk",
        )
    )


def _conversation_rows(search=""):
    """Show one inbox row per contact while keeping each channel in the panel."""
    grouped = {}
    for conversation in _conversation_queryset(search):
        contact = conversation.contact
        preferred = contact.resolved_channel
        has_messages = conversation.latest_message_at is not None
        activity = (
            conversation.latest_message_at
            or conversation.last_message_at
            or conversation.created_at
        )
        candidate_rank = (
            0 if has_messages else 1,
            -activity.timestamp(),
            0 if conversation.channel == preferred else 1,
            -conversation.pk,
        )
        row = grouped.setdefault(contact.pk, {
            "conversation": conversation,
            "channel": conversation.get_channel_display(),
            "preview_body": conversation.latest_message_body,
            "unread_count": 0,
            "activity": activity,
            "has_messages": has_messages,
            "rank": candidate_rank,
        })
        row["unread_count"] += max(conversation.unread_total, int(conversation.marked_unread))
        row["is_finished"] = row.get("is_finished", True) and conversation.status == Conversation.Status.CLOSED
        if candidate_rank < row["rank"]:
            row.update({
                "conversation": conversation,
                "channel": conversation.get_channel_display(),
                "preview_body": conversation.latest_message_body,
                "activity": activity,
                "has_messages": has_messages,
                "rank": candidate_rank,
            })
    return sorted(
        grouped.values(),
        key=lambda row: (
            0 if row["unread_count"] else 1,
            0 if row["has_messages"] else 1,
            -row["activity"].timestamp(),
            -row["conversation"].pk,
        ),
    )


def _mark_read(conversation, *, explicit=False):
    if conversation.marked_unread and not explicit:
        return
    if explicit:
        Conversation.objects.filter(pk=conversation.pk).update(marked_unread=False)
        conversation.marked_unread = False
    conversation.messages.filter(
        direction=Message.Direction.INCOMING,
        is_read=False,
    ).update(is_read=True)


def _channel_available(contact, channel):
    if channel in {
        Conversation.Channel.SMS,
        Conversation.Channel.WHATSAPP,
    }:
        return bool(contact.phone_number)
    if channel == Conversation.Channel.EMAIL:
        return bool(contact.email)
    return False


def _channel_tabs(selected):
    conversations = {
        conversation.channel: conversation
        for conversation in Conversation.objects.filter(contact=selected.contact).annotate(
            unread_total=Count(
                "messages",
                filter=Q(
                    messages__direction=Message.Direction.INCOMING,
                    messages__is_read=False,
                ),
            )
        )
    }
    available_channels = {
        channel: _channel_available(selected.contact, channel)
        for channel, _label in Conversation.Channel.choices
    }
    tabs = []
    for value, label in Conversation.Channel.choices:
        conversation = conversations.get(value)
        tabs.append(
            {
                "value": value,
                "label": label,
                "conversation": conversation,
                "available": available_channels[value],
                "unread_count": max(conversation.unread_total, int(conversation.marked_unread)) if conversation else 0,
                "is_active": conversation == selected,
            }
        )
    return tabs


def _conversation_context(selected, **extra):
    bookings = list(selected.contact.bookings.select_related(
        "booked_tour__responsible",
    ).order_by("-booked_tour__start_time", "-pk"))
    today = timezone.localdate()
    current_bookings = [booking for booking in bookings
                        if booking.attendance != "canceled"
                        and timezone.localtime(booking.booked_tour.start_time).date() >= today]
    for booking in current_bookings:
        booking.modal_data = booking_modal_data(booking.booked_tour, booking)
    history = [
        booking for booking in bookings
        if booking not in current_bookings
        and booking.attendance in {"expected", "present"}
    ]
    templates = [
        template
        for template in MessageTemplate.objects.filter(is_active=True)
        if template.body.strip()
    ]
    template_values = {
        "guest_name": selected.contact.name,
        "guest_first_name": (selected.contact.name or "").split(" ", 1)[0],
        "guide_first_name": "",
        "tour_name": "",
        "booking_time": "",
    }
    # Product decision deferred: template context when several bookings exist.
    booking = bookings[0] if bookings else None
    if booking:
        template_values.update({
            "guest_name": booking.full_name,
            "guest_first_name": booking.first_name,
            "tour_name": booking.booked_tour.name,
            "booking_time": timezone.localtime(booking.booked_tour.start_time).strftime("%b %-d, %-I:%M %p"),
            "guide_first_name": (
                booking.booked_tour.responsible.first_name
                if booking.booked_tour.responsible else ""
            ),
        })

    return {
        "selected": selected,
        "current_bookings": sorted(current_bookings, key=lambda booking: booking.booked_tour.start_time),
        "booking_history": history,
        "is_finished": not selected.contact.conversations.filter(status=Conversation.Status.OPEN).exists(),
        "channel_tabs": _channel_tabs(selected),
        "message_templates": templates,
        "template_values": template_values,
        "booking_form": BookingForm(),
        **extra,
    }


@login_required
def inbox(request):
    conversation_search = request.GET.get("q", "").strip()
    conversations = _conversation_queryset(conversation_search)
    selected = None

    selected_id = request.GET.get("conversation")
    if selected_id:
        selected = get_object_or_404(_conversation_queryset(), pk=selected_id)
    elif request.GET.get("no_selection") != "1":
        selected = conversations.first()

    if selected:
        _mark_read(selected, explicit=True)

    context = {
        "conversation_rows": _conversation_rows(conversation_search),
        "conversation_search": conversation_search,
        "selected": selected,
    }
    if selected:
        context.update(_conversation_context(selected, message_form=MessageForm()))
    return render(request, "messaging/inbox.html", context)


@require_GET
@login_required
def conversation_list(request):
    conversation_search = request.GET.get("q", "").strip()
    return render(
        request,
        "messaging/partials/conversation_list.html",
        {
            "conversation_rows": _conversation_rows(conversation_search),
            "conversation_search": conversation_search,
        },
    )


@require_GET
@login_required
def conversation(request, pk):
    selected = get_object_or_404(_conversation_queryset(), pk=pk)
    _mark_read(selected, explicit=True)

    return render(
        request,
        "messaging/partials/conversation_panel.html",
        _conversation_context(selected, message_form=MessageForm()),
    )


@require_GET
@login_required
def message_list(request, pk):
    selected = get_object_or_404(
        _conversation_queryset().prefetch_related(
            Prefetch("messages", queryset=Message.objects.select_related("sent_by"))
        ),
        pk=pk,
    )
    if request.headers.get("X-Page-Visible") != "no":
        _mark_read(selected)

    return render(
        request,
        "messaging/partials/message_list.html",
        {"selected": selected, "channel_tabs": _channel_tabs(selected), "tabs_oob": True},
    )


@require_POST
@login_required
def send(request, pk):
    selected = get_object_or_404(_conversation_queryset(), pk=pk)
    form = MessageForm(request.POST)

    if form.is_valid() and not _channel_available(selected.contact, selected.channel):
        form.add_error(
            None,
            f"Add the guest's {selected.get_channel_display().lower()} contact information before sending.",
        )

    if form.is_valid():
        body = form.cleaned_data["body"]

        try:
            result = send_message(selected, body)
            message = Message.objects.create(
                conversation=selected,
                direction=Message.Direction.OUTGOING,
                body=body,
                provider_sid=result.provider_sid,
                sent_by=request.user,
                sender_name=request.user.get_full_name() or request.user.get_username(),
                status=result.status,
                is_read=True,
            )
        except Exception as exc:
            message = Message.objects.create(
                conversation=selected,
                direction=Message.Direction.OUTGOING,
                body=body,
                status=Message.Status.FAILED,
                sent_by=request.user,
                sender_name=request.user.get_full_name() or request.user.get_username(),
                is_read=True,
                error_message=str(exc),
            )

        selected.last_message_at = message.created_at
        selected.save(update_fields=["last_message_at"])

        form = MessageForm()

    return render(
        request,
        "messaging/partials/conversation_panel.html",
        _conversation_context(selected, message_form=form),
    )


@require_POST
@login_required
def open_channel(request, contact_pk, channel):
    if channel not in {value for value, _label in Conversation.Channel.choices}:
        return HttpResponseBadRequest("Unknown messaging channel.")

    contact = get_object_or_404(Guest.objects.filter(bookings__isnull=False).distinct(), pk=contact_pk)
    if not _channel_available(contact, channel):
        return HttpResponseBadRequest("Contact information is required for this channel.")

    conversation_obj, _ = Conversation.objects.get_or_create(
        contact=contact,
        channel=channel,
    )
    return redirect(
        f"{reverse('messaging:inbox')}?conversation={conversation_obj.pk}"
    )


@require_POST
@login_required
@transaction.atomic
def interaction(request, pk):
    selected = get_object_or_404(_conversation_queryset(), pk=pk)
    channels = Conversation.objects.filter(contact=selected.contact)
    action = request.POST.get("action")
    if action == "unread":
        channels.update(status=Conversation.Status.OPEN)
        Conversation.objects.filter(pk=selected.pk).update(marked_unread=True)
        return redirect(f"{reverse('messaging:inbox')}?no_selection=1")
    if action == "finish":
        channels.update(status=Conversation.Status.CLOSED, marked_unread=False)
        Message.objects.filter(
            conversation__contact=selected.contact, direction=Message.Direction.INCOMING,
        ).update(is_read=True)
    elif action == "reopen":
        channels.update(status=Conversation.Status.OPEN)
    else:
        return HttpResponseBadRequest("Unknown interaction action.")
    return redirect(f"{reverse('messaging:inbox')}?conversation={selected.pk}")


def _twilio_request_is_valid(request):
    if not settings.TWILIO_VALIDATE_WEBHOOKS:
        return True

    if not settings.TWILIO_AUTH_TOKEN:
        return False

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        return False

    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    url = request.build_absolute_uri()
    return validator.validate(url, request.POST, signature)


def _strip_channel_prefix(number):
    return (number or "").removeprefix("whatsapp:")


@csrf_exempt
@require_POST
@transaction.atomic
def twilio_inbound(request):
    if not _twilio_request_is_valid(request):
        return HttpResponseForbidden("Invalid Twilio signature.")

    sender = request.POST.get("From", "")
    body = request.POST.get("Body", "")
    provider_sid = request.POST.get("MessageSid", "")

    if not sender:
        return HttpResponseBadRequest("Missing From.")

    channel = (
        Conversation.Channel.WHATSAPP
        if sender.startswith("whatsapp:")
        else Conversation.Channel.SMS
    )
    phone_number = _strip_channel_prefix(sender)

    contact, _ = Guest.objects.get_or_create(
        phone_number=phone_number,
        defaults={"name": phone_number},
    )

    conversation_obj, _ = Conversation.objects.get_or_create(
        contact=contact,
        channel=channel,
    )

    existing = (
        Message.objects.filter(provider_sid=provider_sid).first()
        if provider_sid
        else None
    )

    if not existing:
        incoming = Message.objects.create(
            conversation=conversation_obj,
            direction=Message.Direction.INCOMING,
            body=body,
            provider_sid=provider_sid or None,
            status=Message.Status.RECEIVED,
            is_read=False,
        )
        conversation_obj.last_message_at = incoming.created_at
        conversation_obj.save(update_fields=["last_message_at"])
        contact.last_inbound_channel = channel
        contact.last_inbound_at = incoming.created_at
        contact.save(update_fields=["last_inbound_channel", "last_inbound_at"])

    return HttpResponse("<Response></Response>", content_type="text/xml")


@csrf_exempt
@require_POST
def twilio_status(request):
    if not _twilio_request_is_valid(request):
        return HttpResponseForbidden("Invalid Twilio signature.")

    provider_sid = request.POST.get("MessageSid", "")
    provider_status = request.POST.get("MessageStatus", "")

    if not provider_sid:
        return HttpResponseBadRequest("Missing MessageSid.")

    known_statuses = {value for value, _label in Message.Status.choices}
    if provider_status in known_statuses:
        message = Message.objects.filter(provider_sid=provider_sid).first()
        if message:
            terminal = {Message.Status.DELIVERED, Message.Status.READ, Message.Status.FAILED, Message.Status.UNDELIVERED}
            ranks = {"queued": 0, "accepted": 1, "sending": 2, "sent": 3, "delivered": 4, "read": 5}
            if (message.status not in terminal or provider_status == "read") and (
                provider_status in terminal or ranks.get(provider_status, -1) >= ranks.get(message.status, -1)
            ):
                error_code = request.POST.get("ErrorCode", "")
                Message.objects.filter(pk=message.pk).update(
                    status=provider_status,
                    error_message=f"Twilio delivery error {error_code}." if error_code else "",
                )
                from mytours.models import ThankYouDelivery
                ThankYouDelivery.objects.filter(message=message, state__in=["review", "sending"]).update(state="complete")

    return HttpResponse("ok")
