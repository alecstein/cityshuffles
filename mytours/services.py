import uuid
import json

from django.db import transaction

from bookings.models import Guest, Tour
from integrations.models import Connection, VendorBooking
from messaging.models import Contact
from django.utils import timezone


def booking_modal_data(tour, guest=None):
    """Preload each booking's form data; opening a modal never fetches it."""
    vendor = getattr(guest, "vendorbooking", None) if guest else None
    start = timezone.localtime(tour.start_time)
    fields = {
        "tour": tour.pk,
        "tour_date": start.strftime("%Y-%m-%d"),
        "tour_time": start.strftime("%H:%M"),
        "guide": (tour.responsible.get_full_name() or tour.responsible.username) if tour.responsible else "Unassigned",
        "vendor": vendor.display_vendor if vendor else ("Manual/Walk-up" if not guest or guest.is_manual else "Direct"),
        "booking_code": vendor.external_id if vendor else (str(guest.pk) if guest else "Assigned on save"),
        "event_id": vendor.event.external_id if vendor and vendor.event_id else str(tour.pk),
        "name": guest.full_name if guest else "",
        "phone_number": (guest.contact.phone_number or "") if guest else "",
        "email": (guest.contact.email or guest.email or "") if guest else "",
        "adults": guest.adults if guest else 1,
        "children": guest.children if guest else 0,
        "original_adults": guest.original_adults if guest else 1,
        "original_children": guest.original_children if guest else 0,
        "special_requests": guest.special_requests if guest else "",
        "language": guest.language if guest else "",
        "tour_notes": guest.tour_notes if guest else "",
    }
    return json.dumps(fields)


@transaction.atomic
def create_manual_booking(tour: Tour, form, contact_override=None):
    """Create the local guest and vendor-side records for a walk-up booking."""
    tour = form.cleaned_data.get("tour") or tour
    name = form.cleaned_data["name"].strip()
    first_name, _, last_name = name.partition(" ")
    phone = form.cleaned_data["phone_number"]
    email = form.cleaned_data["email"] or None
    contact = contact_override or (Contact.objects.filter(phone_number=phone).first() if phone else None)
    if contact is None and email:
        contact = Contact.objects.filter(email=email).first()
    if contact is None:
        contact = Contact.objects.create(
            name=name,
            phone_number=phone,
            email=email,
        )
    else:
        changed_fields = []
        if contact.name != name:
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

    adults = form.cleaned_data["adults"]
    children = form.cleaned_data["children"]
    guest = Guest.objects.create(
        first_name=first_name[:80],
        last_name=last_name[:80],
        email=email,
        contact=contact,
        booked_tour=tour,
        imported=True,
        is_manual=True,
        adults=adults,
        children=children,
        original_adults=adults,
        original_children=children,
        attendance=form.cleaned_data.get("attendance") or Guest.Attendance.EXPECTED,
        special_requests=form.cleaned_data.get("special_requests", ""),
        language=form.cleaned_data.get("language", ""),
        tour_notes=form.cleaned_data.get("tour_notes", ""),
        booking_issue=form.cleaned_data.get("booking_issue", ""),
    )
    connection, _ = Connection.objects.get_or_create(
        vendor="manual",
        defaults={"enabled": True, "auth_status": "ok", "sync_status": "ok"},
    )
    VendorBooking.objects.create(
        connection=connection,
        external_id=f"manual-{uuid.uuid4().hex}",
        booking=guest,
        source={
            "vendor": "manual",
            "name": name,
            "phone": phone or "",
            "email": email or "",
            "adults": adults,
            "children": children,
            "status": "confirmed",
            "manual": True,
            "special_requests": guest.special_requests,
            "language": guest.language,
        },
    )
    return guest


@transaction.atomic
def update_booking(guest: Guest, form, sender=None):
    """Update the shared guest/contact details used by the booking modal."""
    target = form.cleaned_data.get("tour") or guest.booked_tour
    if target.pk != guest.booked_tour_id:
        guest = Guest.objects.select_for_update().get(pk=guest.pk)
        existing = Guest.objects.filter(rescheduled_from=guest).first()
        if existing:
            return existing
        replacement = create_manual_booking(target, form, contact_override=guest.contact)
        replacement.rescheduled_from = guest
        replacement.save(update_fields=["rescheduled_from"])
        original_vendor = getattr(guest, "vendorbooking", None)
        if original_vendor and original_vendor.is_mock:
            VendorBooking.objects.filter(booking=replacement).update(is_mock=True)
        guest.attendance = Guest.Attendance.CANCELED
        guest.attendance_overridden = True
        guest.save(update_fields=["attendance", "attendance_overridden"])
        from bookings.services import send_welcome_for_guest
        transaction.on_commit(lambda: send_welcome_for_guest(replacement.pk, force=True, sender=sender))
        return replacement
    name = form.cleaned_data["name"].strip()
    first_name, _, last_name = name.partition(" ")
    phone = form.cleaned_data["phone_number"]
    email = form.cleaned_data["email"] or None
    adults = form.cleaned_data["adults"]
    children = form.cleaned_data["children"]

    contact = guest.contact
    contact.name = name
    contact.phone_number = phone
    contact.email = email
    contact.save(update_fields=["name", "phone_number", "email"])

    count_changed = guest.adults != adults or guest.children != children
    guest.first_name = first_name[:80]
    guest.last_name = last_name[:80]
    guest.email = email
    guest.adults = adults
    guest.children = children
    if count_changed:
        guest.party_size_overridden = True
    attendance = form.cleaned_data.get("attendance") or guest.attendance
    if attendance != guest.attendance:
        guest.attendance_overridden = True
    guest.attendance = attendance
    for field in ("special_requests", "language", "tour_notes"):
        setattr(guest, field, form.cleaned_data[field])
    guest.save(update_fields=[
        "first_name", "last_name", "email", "adults", "children", "party_size_overridden",
        "attendance", "attendance_overridden", "special_requests", "language", "tour_notes", "booking_issue",
    ])
    Guest.objects.filter(contact=contact).exclude(pk=guest.pk).update(
        first_name=guest.first_name, last_name=guest.last_name, email=email,
    )
    return guest
