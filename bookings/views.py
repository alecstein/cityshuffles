from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Prefetch, Case, When, Value, IntegerField, CharField, Exists, OuterRef
from django.utils import timezone
from itertools import groupby
from django.http import HttpResponse, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from mytours.forms import AttendanceForm, BookingForm, ManualBookingForm
from mytours.services import booking_modal_data, create_manual_booking, update_booking
from messaging.models import Message

from .models import Guest, Tour
from .services import (
    open_conversation_for_booking, open_conversation_for_guest,
    start_conversation_for_booking, start_conversation_for_guest, with_chat_state,
)
from integrations.models import VendorBooking


def dashboard_context(add_booking_form=None, add_booking_tour=None):
    guests = with_chat_state(Guest.objects.annotate(canceled_order=Case(
        When(attendance="canceled", then=Value(1)), default=Value(0), output_field=IntegerField()
    ), vendor_order=Case(When(vendorbooking__is_mock=True, then=Value("DemoTours")), When(vendorbooking__connection__vendor="manual", then=Value("Manual/Walk-up")), default="vendorbooking__connection__vendor", output_field=CharField()))).order_by("canceled_order", "vendor_order", "last_name", "first_name", "pk").select_related("contact", "vendorbooking__connection", "vendorbooking__event").prefetch_related(
        Prefetch(
            "contact__conversations",
            to_attr="bookings_conversations",
        )
    )
    tours = Tour.objects.filter(
        start_time__date__gte=timezone.localdate(),
    ).select_related("responsible").prefetch_related(Prefetch("guests", queryset=guests))
    for tour in tours:
        for guest in tour.guests.all():
            guest.modal_data = booking_modal_data(tour, guest)
    date_groups = [{"date": day, "tours": list(items)} for day, items in
                   groupby(tours, key=lambda t: timezone.localtime(t.start_time).date())]
    booking_messages = Message.objects.filter(
        conversation__contact_id=OuterRef("booking__contact_id"),
    )
    new_bookings = (
        VendorBooking.objects
        .filter(is_new=True, conversation_started_at__isnull=True)
        .annotate(chat_exists=Exists(booking_messages))
        .filter(chat_exists=False)
        .exclude(source__status__in=["cancelled", "canceled"])
        .select_related(
            "booking__contact",
            "booking__vendorbooking__connection",
            "event__departure",
            "connection",
        )
        .prefetch_related(
            Prefetch(
                "booking__contact__conversations",
                to_attr="bookings_conversations",
            )
        )
        .order_by("event__departure__start_time", "pk")
    )
    return {
        "tours": tours,
        "date_groups": date_groups,
        "new_bookings": new_bookings,
        "add_booking_form": add_booking_form or ManualBookingForm(),
        "add_booking_tour": add_booking_tour,
    }


@login_required
def index(request):
    return render(request, "bookings/index.html", dashboard_context())


@require_POST
@login_required
def add_booking(request, tour_pk):
    tour = get_object_or_404(Tour, pk=tour_pk)
    form = ManualBookingForm(request.POST)
    if not form.is_valid():
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"errors": form.errors}, status=400)
        return render(
            request,
            "bookings/index.html",
            dashboard_context(form, add_booking_tour=tour),
            status=400,
        )
    create_manual_booking(tour, form)
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"redirect": reverse("bookings:index")})
    return redirect("bookings:index")


@require_POST
@login_required
def start_booking_conversation(request, pk):
    vendor_booking = get_object_or_404(
        VendorBooking.objects.select_related("booking__contact", "connection"),
        pk=pk,
    )
    conversation = start_conversation_for_booking(vendor_booking, request.user)
    if conversation is None:
        messages.error(
            request,
            "This guest has no usable contact method yet, so the welcome message was not sent.",
        )
        return redirect("bookings:index")
    return redirect(f"{reverse('messaging:inbox')}?conversation={conversation.pk}")


@require_POST
@login_required
def open_booking_conversation(request, pk):
    vendor_booking = get_object_or_404(
        VendorBooking.objects.select_related("booking__contact", "connection"),
        pk=pk,
    )
    conversation = open_conversation_for_booking(vendor_booking)
    if conversation is None:
        messages.error(
            request,
            "This guest has no usable contact method yet, so the chat could not be opened.",
        )
        return redirect(request.META.get("HTTP_REFERER") or "bookings:index")
    return redirect(f"{reverse('messaging:inbox')}?conversation={conversation.pk}")


@require_POST
@login_required
def open_guest_conversation(request, pk):
    guest = get_object_or_404(Guest.objects.select_related("contact"), pk=pk)
    conversation = open_conversation_for_guest(guest)
    if conversation is None:
        messages.error(
            request,
            "This guest has no usable contact method yet, so the chat could not be opened.",
        )
        return redirect(request.META.get("HTTP_REFERER") or "bookings:index")
    return redirect(f"{reverse('messaging:inbox')}?conversation={conversation.pk}")


@require_POST
@login_required
def ignore_new_booking(request, pk):
    vendor_booking = get_object_or_404(VendorBooking, pk=pk)
    if vendor_booking.is_new:
        vendor_booking.is_new = False
        vendor_booking.save(update_fields=["is_new"])
    return redirect(request.META.get("HTTP_REFERER") or "bookings:index")


@require_POST
@login_required
def start_guest_conversation(request, pk):
    guest = get_object_or_404(Guest.objects.select_related("contact"), pk=pk)
    vendor_booking = (
        VendorBooking.objects
        .filter(booking_id=guest.pk)
        .select_related("connection")
        .first()
    )
    if vendor_booking:
        conversation = start_conversation_for_booking(vendor_booking, request.user)
    else:
        conversation = start_conversation_for_guest(guest, sender=request.user)
    if conversation is None:
        messages.error(
            request,
            "This guest has no usable contact method yet, so the welcome message was not sent.",
        )
        return redirect(request.META.get("HTTP_REFERER") or "bookings:index")
    return redirect(f"{reverse('messaging:inbox')}?conversation={conversation.pk}")


@require_GET
@login_required
def guest_edit(request, pk):
    guest = get_object_or_404(Guest.objects.select_related("contact", "booked_tour__responsible", "vendorbooking__connection", "vendorbooking__event"), pk=pk)
    return booking_edit_page(request, guest, BookingForm(guest=guest))


def booking_edit_page(request, guest, form, status=200):
    return render(request, "bookings/booking_edit.html", {
        "add_form": form, "modal_mode": "edit", "modal_open": True,
        "modal_tour": guest.booked_tour, "modal_data": booking_modal_data(guest.booked_tour, guest),
        "action_url": reverse("bookings:guest_update", args=[guest.pk]),
        "delete_action_url": reverse("bookings:guest_delete", args=[guest.pk]) if guest.is_manual else "",
        "submit_label": "Save booking",
    }, status=status)


@require_POST
@login_required
def guest_update(request, pk):
    guest = get_object_or_404(Guest.objects.select_related("contact", "booked_tour__responsible", "vendorbooking__connection", "vendorbooking__event"), pk=pk)
    form = BookingForm(request.POST, guest=guest)
    if form.is_valid():
        update_booking(guest, form, sender=request.user)
        target = request.POST.get("return_url", "")
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            target = reverse("bookings:index")
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"redirect": target})
        return redirect(target)
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"errors": form.errors}, status=400)
    return booking_edit_page(request, guest, form, status=400)


@require_POST
@login_required
def guest_attendance_update(request, pk):
    guest = get_object_or_404(
        Guest.objects.select_related(
            "booked_tour", "contact", "vendorbooking__connection", "vendorbooking__event",
        ).prefetch_related(
            Prefetch("contact__conversations", to_attr="bookings_conversations"),
        ),
        pk=pk,
    )
    form = AttendanceForm(request.POST, instance=guest)
    if not form.is_valid():
        return HttpResponse("Invalid booking status.", status=400)
    guest = form.save(commit=False)
    guest.attendance_overridden = True
    guest.save(update_fields=["attendance", "attendance_overridden"])
    if request.headers.get("HX-Request"):
        guest.modal_data = booking_modal_data(guest.booked_tour, guest)
        return render(request, "bookings/partials/guest_row.html", {"guest": guest})
    return redirect("bookings:index")


@require_POST
@login_required
def guest_delete(request, pk):
    guest = get_object_or_404(
        Guest.objects.select_related("booked_tour"),
        pk=pk,
        is_manual=True,
    )
    VendorBooking.objects.filter(booking=guest).delete()
    guest.delete()
    if request.headers.get("HX-Request"):
        return HttpResponse(headers={"HX-Refresh": "true"})
    return redirect(request.META.get("HTTP_REFERER") or "bookings:index")
