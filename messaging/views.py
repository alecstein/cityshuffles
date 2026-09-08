from django.conf import settings
from django.contrib import messages as django_messages
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

from .forms import ContactEditForm, MessageForm, NewConversationForm
from .models import Contact, Conversation, Message
from .services import send_message
from message_templates.models import MessageTemplate


@login_required
@require_GET
def unread_badge(request):
    return render(request, "messaging/partials/nav_unread.html")


def _conversation_queryset(search=""):
    queryset = Conversation.objects.select_related("contact")
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
        row["unread_count"] += conversation.unread_total
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
            0 if row["has_messages"] else 1,
            -row["activity"].timestamp(),
            -row["conversation"].pk,
        ),
    )


def _mark_read(conversation):
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
                "unread_count": conversation.unread_total if conversation else 0,
                "is_active": conversation == selected,
            }
        )
    return tabs


def _conversation_context(selected, **extra):
    templates = []
    for template in MessageTemplate.objects.filter(is_active=True):
        template.channel_body = template.body
        if template.channel_body.strip():
            templates.append(template)
    template_values = {
        "guest_name": selected.contact.name,
        "guest_first_name": (selected.contact.name or "").split(" ", 1)[0],
        "guide_first_name": "",
        "tour_name": "",
        "booking_time": "",
    }
    try:
        from bookings.models import Guest

        guest = (
            Guest.objects
            .filter(contact=selected.contact)
            .select_related("booked_tour__responsible")
            .order_by("-booked_tour__start_time", "-pk")
            .first()
        )
        if guest:
            template_values.update({
                "guest_name": guest.full_name,
                "guest_first_name": guest.first_name,
                "tour_name": guest.booked_tour.name,
                "booking_time": timezone.localtime(guest.booked_tour.start_time).strftime("%b %-d, %-I:%M %p"),
                "guide_first_name": (
                    guest.booked_tour.responsible.first_name
                    if guest.booked_tour.responsible else ""
                ),
            })
    except (AttributeError, ValueError):
        # A conversation may exist before it is linked to a booking.
        pass

    return {
        "selected": selected,
        "channel_tabs": _channel_tabs(selected),
        "contact_form": ContactEditForm(instance=selected.contact),
        "message_templates": templates,
        "template_values": template_values,
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
    else:
        selected = conversations.first()

    if selected:
        _mark_read(selected)

    context = {
        "conversations": conversations,
        "conversation_rows": _conversation_rows(conversation_search),
        "conversation_search": conversation_search,
        "selected": selected,
        "new_conversation_form": NewConversationForm(),
    }
    if selected:
        context.update(_conversation_context(selected, message_form=MessageForm()))
    else:
        context.update({
            "channel_tabs": [],
            "contact_form": None,
            "message_form": MessageForm(),
            "message_templates": MessageTemplate.objects.filter(is_active=True),
        })
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
    _mark_read(selected)

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
def edit_contact(request, pk):
    selected = get_object_or_404(_conversation_queryset(), pk=pk)
    form = ContactEditForm(request.POST, instance=selected.contact)
    if form.is_valid():
        form.save()
        if request.headers.get("HX-Request"):
            return HttpResponse(headers={"HX-Refresh": "true"})
        return redirect(f"{reverse('messaging:inbox')}?conversation={selected.pk}")
    return render(request, "messaging/partials/conversation_panel.html",
                  _conversation_context(selected, message_form=MessageForm(),
                                        contact_form=form, contact_edit_open=True))


@require_POST
@login_required
def open_channel(request, contact_pk, channel):
    if channel not in {value for value, _label in Conversation.Channel.choices}:
        return HttpResponseBadRequest("Unknown messaging channel.")

    contact = get_object_or_404(Contact, pk=contact_pk)
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
def new_conversation(request):
    form = NewConversationForm(request.POST)

    if not form.is_valid():
        django_messages.error(request, "Please check the new-conversation fields.")
        return redirect("messaging:inbox")

    phone = form.cleaned_data["phone_number"] or None
    email = form.cleaned_data["email"] or None
    name = form.cleaned_data["name"].strip() or email or phone
    channel = form.cleaned_data["channel"]

    lookup = {"phone_number": phone} if phone else {"email": email}
    contact, created = Contact.objects.get_or_create(
        defaults={"name": name, "phone_number": phone, "email": email},
        **lookup,
    )

    if not created:
        changed_fields = []
        if name and contact.name != name:
            contact.name = name
            changed_fields.append("name")
        if phone and contact.phone_number != phone:
            contact.phone_number = phone
            changed_fields.append("phone_number")
        if email and contact.email != email:
            contact.email = email
            changed_fields.append("email")
        if changed_fields:
            contact.save(update_fields=changed_fields)

    conversation_obj, _ = Conversation.objects.get_or_create(
        contact=contact,
        channel=channel,
    )

    return redirect(
        f"{reverse('messaging:inbox')}?conversation={conversation_obj.pk}"
    )


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

    contact, _ = Contact.objects.get_or_create(
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
