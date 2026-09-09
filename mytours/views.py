import calendar
import uuid
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Case, IntegerField, Prefetch, Value, When
from django.http import HttpResponseBadRequest, JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from bookings.models import Booking, Tour
from bookings.services import with_chat_state
from .forms import AttendanceForm, ManualBookingForm
from .services import booking_modal_data, create_manual_booking
from .models import ThankYouAction, ThankYouDelivery
from .thank_you import request_thank_you, retry_delivery, DEFAULT_THANK_YOU
from message_templates.models import MessageTemplate


def accessible_tours(user):
    tours = Tour.objects.select_related("responsible")
    return tours if user.is_staff else tours.filter(responsible=user)


def tour_url(tour, request=None):
    from urllib.parse import urlsplit
    all_view = request and request.user.is_staff and (tour.responsible_id != request.user.pk or urlsplit(request.META.get("HTTP_REFERER", "")).path == reverse("mytours:all"))
    route = "mytours:all" if all_view else "mytours:index"
    return f"{reverse(route)}?date={timezone.localtime(tour.start_time).date().isoformat()}&tour={tour.pk}"


def guest_rows(tour, invalid_guest=None, invalid_form=None, row_order=""):
    guests = with_chat_state(tour.guests.annotate(canceled_order=Case(
        When(attendance="canceled", then=Value(1)), default=Value(0), output_field=IntegerField()
    ))).order_by("canceled_order", "last_name", "first_name", "pk").select_related("contact", "vendorbooking__connection", "vendorbooking__event").prefetch_related(
        Prefetch("contact__conversations", to_attr="bookings_conversations")
    )
    guests = list(guests)
    tour._display_guests = guests
    if row_order:
        positions = {int(pk): i for i, pk in enumerate(row_order.split(",")) if pk.isdigit()}
        guests.sort(key=lambda guest: positions.get(guest.pk, len(positions)))
    for guest in guests:
        guest.modal_data = booking_modal_data(tour, guest)
    return [{
        "guest": g,
        "tour": tour,
        "form": invalid_form if g.pk == invalid_guest else AttendanceForm(instance=g),
    } for g in guests]


def page_context(request, day, selected, add_booking_form=None, all_tours=False):
    assigned = accessible_tours(request.user) if all_tours else Tour.objects.filter(responsible=request.user)
    first = day.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    prev_month = (first - timedelta(days=1)).replace(day=1)
    tours = assigned.filter(start_time__date=day)
    tour_dates = {
        timezone.localtime(t.start_time).date()
        for t in assigned.filter(
            start_time__date__gte=first,
            start_time__date__lt=next_month,
        )
    }
    today = timezone.localdate()
    weeks = [
        [
            {
                "date": d,
                "selected": d == day,
                "today": d == today,
                "has_tours": d in tour_dates,
                "in_month": d.month == day.month,
            }
            for d in week
        ]
        for week in calendar.Calendar().monthdatescalendar(day.year, day.month)
    ]
    if add_booking_form is None:
        add_booking_form = ManualBookingForm()
    if selected:
        selected.modal_data = booking_modal_data(selected)
    return {
        "all_tours": all_tours,
        "page_title": "All Tours" if all_tours else "My Tours",
        "day": day,
        "today": today,
        "weeks": weeks,
        "previous": prev_month,
        "next": next_month,
        "tours": tours,
        "selected": selected,
        "guest_rows": guest_rows(selected) if selected else [],
        "add_booking_form": add_booking_form,
        **group_message_context(selected),
        "custom_request_key": uuid.uuid4(),
    }


@login_required
def index(request, all_tours=False):
    if all_tours and not request.user.is_staff:
        return HttpResponseForbidden("Admins only.")
    assigned = accessible_tours(request.user) if all_tours else Tour.objects.filter(responsible=request.user)
    today = timezone.localdate()
    try:
        day = date.fromisoformat(request.GET.get("date", today.isoformat()))
    except ValueError:
        return HttpResponseBadRequest("Invalid date.")
    tours = assigned.filter(start_time__date=day)
    selected = get_object_or_404(tours, pk=request.GET["tour"]) if request.GET.get("tour") else tours.first()
    return render(request, "mytours/index.html", page_context(request, day, selected, all_tours=all_tours))


@require_POST
@login_required
def add_booking(request, tour_pk):
    tour = get_object_or_404(accessible_tours(request.user), pk=tour_pk)
    form = ManualBookingForm(request.POST)
    if not form.is_valid():
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"errors": form.errors}, status=400)
        day = timezone.localtime(tour.start_time).date()
        return render(
            request,
            "mytours/index.html",
            page_context(request, day, tour, add_booking_form=form),
            status=400,
        )
    destination = form.cleaned_data.get("tour") or tour
    get_object_or_404(accessible_tours(request.user), pk=destination.pk)
    create_manual_booking(tour, form)
    tour = destination
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"redirect": tour_url(tour, request)})
    return redirect(tour_url(tour, request))


def thank_you_context(tour):
    action = ThankYouAction.objects.filter(tour=tour, kind="closing").first() if tour else None
    deliveries = list(action.deliveries.select_related("message__conversation")) if action else []
    return {
        "thank_you_action": action, "thank_you_deliveries": deliveries,
        "thank_you_pending": any(
            d.state in {"pending", "sending"}
            or (d.message and d.message.status in {"queued", "accepted", "sending"})
            for d in deliveries
        ),
        "delivery_expanded": True,
        "thank_you_templates": MessageTemplate.objects.filter(is_active=True, channel="any"),
        "default_thank_you": DEFAULT_THANK_YOU,
    }


@require_POST
@login_required
def send_thank_you(request, tour_pk):
    tour = get_object_or_404(accessible_tours(request.user), pk=tour_pk)
    template = None
    if request.POST.get("template"):
        template = get_object_or_404(MessageTemplate, pk=request.POST["template"], is_active=True, channel="any")
    error = ""
    try:
        request_thank_you(tour, request.user, template)
    except ValueError as exc:
        error = str(exc)
        if not request.headers.get("HX-Request"):
            messages.error(request, error)
    if request.headers.get("HX-Request"):
        return render(request, "mytours/partials/thank_you_action.html", {"selected": tour, "thank_you_error": error, **thank_you_context(tour)})
    return redirect(tour_url(tour, request))


def group_message_context(tour, expanded=()):
    from photos.models import Album
    album = Album.objects.filter(tour=tour).first() if tour else None
    actions = list(ThankYouAction.objects.filter(tour=tour).order_by("-created_at", "-pk").prefetch_related("deliveries__message__conversation")) if tour else []
    for action in actions:
        action.expanded = str(action.pk) in expanded
    closing = next((a for a in actions if a.kind == "closing"), None)
    return {"photo_album": album, "photos_ready": bool(album and album.photos.exists()), "photos_action": next((a for a in actions if a.kind == "photos"), None), "group_actions": actions, "closing_action": closing,
            "closing_template": MessageTemplate.objects.filter(system_key="closing").first()}


@login_required
@require_GET
def group_history(request, tour_pk):
    tour = get_object_or_404(accessible_tours(request.user), pk=tour_pk)
    return render(request, "mytours/partials/group_history.html", {
        "selected": tour, "update_quick_actions": True,
        **group_message_context(tour, request.GET.getlist("expanded")),
    })


@login_required
@require_POST
def send_group_message(request, tour_pk):
    tour = get_object_or_404(accessible_tours(request.user), pk=tour_pk)
    kind = request.POST.get("kind", "")
    error = ""
    try:
        template = get_object_or_404(MessageTemplate, system_key=kind) if kind in {"closing", "photos"} else None
        if template and not template.is_active:
            raise ValueError("Activate the Closing message template before sending.")
        request_key = uuid.UUID(request.POST.get("request_key", "")) if kind == "custom" else None
        photos_url = ""
        if kind == "photos":
            from django.conf import settings
            from photos.models import Album
            album = Album.objects.filter(tour=tour).first()
            if not album or not album.photos.exists():
                raise ValueError("Upload photos before sending the gallery link.")
            photos_url = (settings.PUBLIC_BASE_URL or request.build_absolute_uri("/").rstrip("/")) + reverse("photos:album", args=[album.token])
        request_thank_you(tour, request.user, template, kind=kind, body=request.POST.get("body", ""), request_key=request_key, photos_url=photos_url)
    except ValueError as exc:
        error = str(exc)
    if not request.headers.get("HX-Request"):
        if error:
            messages.error(request, error)
        return redirect(tour_url(tour, request))
    response = render(request, "mytours/partials/group_history.html", {
        "selected": tour, "group_error": error, "update_quick_actions": True,
        **group_message_context(tour, request.POST.getlist("expanded")),
    })
    if not error and kind == "custom":
        import json
        response["HX-Trigger"] = json.dumps({"group-message-sent": {"requestKey": str(uuid.uuid4())}})
    return response


@require_GET
@login_required
def thank_you_status(request, tour_pk):
    tour = get_object_or_404(accessible_tours(request.user), pk=tour_pk)
    return render(request, "mytours/partials/thank_you_action.html", {
        "selected": tour, **thank_you_context(tour),
        "delivery_expanded": request.GET.get("expanded", "1") != "0",
    })


@require_POST
@login_required
def retry_thank_you(request, pk):
    delivery = get_object_or_404(ThankYouDelivery.objects.select_related("action__tour"), pk=pk, action__tour__in=accessible_tours(request.user))
    retry_delivery(delivery)
    tour = delivery.action.tour
    if request.headers.get("HX-Request"):
        if request.POST.get("group_history"):
            return render(request, "mytours/partials/group_history.html", {
                "selected": tour, "update_quick_actions": True,
                **group_message_context(tour, request.POST.getlist("expanded")),
            })
        return render(request, "mytours/partials/thank_you_action.html", {"selected": tour, **thank_you_context(tour)})
    return redirect(tour_url(tour, request))


@require_POST
@login_required
def guest_update(request, pk):
    guest = get_object_or_404(Booking.objects.select_related("booked_tour", "contact"),
                              pk=pk, booked_tour__in=accessible_tours(request.user))
    form = AttendanceForm(request.POST, instance=guest)
    saved = form.is_valid()
    if saved:
        guest = form.save(commit=False)
        guest.attendance_overridden = True
        guest.save()
    if request.headers.get("HX-Request"):
        return render(request, "mytours/parties.html", {"guest_rows": guest_rows(
            guest.booked_tour, guest.pk if not saved else None, form if not saved else None,
            row_order=request.POST.get("row_order", "")), "total_tour": guest.booked_tour, "update_total": True})
    if not saved:
        return HttpResponseBadRequest("Invalid booking status.")
    return redirect(tour_url(guest.booked_tour, request))


def party_response(request, tour):
    if request.headers.get("HX-Request"):
        return render(request, "mytours/parties.html", {"guest_rows": guest_rows(tour, row_order=request.POST.get("row_order", "")), "total_tour": tour, "update_total": True})
    return redirect(tour_url(tour, request))


@require_POST
@login_required
def guest_party_update(request, pk):
    guest = get_object_or_404(
        Booking.objects.select_related("booked_tour"),
        pk=pk,
        booked_tour__in=accessible_tours(request.user),
    )
    field = "adults_delta" if "adults_delta" in request.POST else "children_delta"
    try:
        delta = int(request.POST.get(field, ""))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid party count.")
    if delta not in {-1, 1}:
        return HttpResponseBadRequest("Invalid party count.")

    count_field = "adults" if field == "adults_delta" else "children"
    new_count = max(0, getattr(guest, count_field) + delta)
    setattr(guest, count_field, new_count)
    guest.party_size_overridden = True
    guest.save(update_fields=[count_field, "party_size_overridden"])
    return party_response(request, guest.booked_tour)


@require_POST
@login_required
def guest_feedback(request, pk):
    guest = get_object_or_404(
        Booking.objects.select_related("booked_tour"),
        pk=pk,
        booked_tour__in=accessible_tours(request.user),
    )
    cycle = {
        Booking.Feedback.NONE: Booking.Feedback.UP,
        Booking.Feedback.UP: Booking.Feedback.DOWN,
        Booking.Feedback.DOWN: Booking.Feedback.NONE,
    }
    guest.feedback = cycle[guest.feedback]
    guest.save(update_fields=["feedback"])
    return party_response(request, guest.booked_tour)
