from django import forms
from bookings.models import Guest, Tour
from django.utils import timezone
from messaging.models import Contact


class AttendanceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["attendance"].choices = [
            (
                value,
                "Not checked in" if value == Guest.Attendance.EXPECTED else label,
            )
            for value, label in self.fields["attendance"].choices
        ]

    class Meta:
        model = Guest
        fields = ("attendance",)


class DepartureSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if hasattr(value, "instance"):
            tour = value.instance
            start = timezone.localtime(tour.start_time)
            option["attrs"].update({"data-date": start.strftime("%Y-%m-%d"), "data-time": start.strftime("%H:%M"), "data-guide": (tour.responsible.get_full_name() or tour.responsible.username) if tour.responsible else "Unassigned"})
        return option


class DepartureField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} — {timezone.localtime(obj.start_time):%b %d, %Y · %I:%M %p}"


class BookingForm(forms.Form):
    tour = DepartureField(queryset=Tour.objects.none(), required=False, empty_label=None, widget=DepartureSelect)
    name = forms.CharField(max_length=160, label="Guest name")
    phone_number = forms.CharField(
        max_length=40,
        required=False,
        label="Phone number",
        help_text="Optional. Format: +[country code][phone number].",
    )
    email = forms.EmailField(required=False)
    adults = forms.IntegerField(min_value=0, initial=1)
    children = forms.IntegerField(min_value=0, initial=0)
    special_requests = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    language = forms.CharField(max_length=80, required=False)
    tour_notes = forms.CharField(required=False, label="Staff notes", widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, guest=None, **kwargs):
        self.guest = guest
        super().__init__(*args, **kwargs)
        self.fields["tour"].queryset = Tour.objects.select_related("responsible").order_by("start_time", "name")
        if guest is not None and not self.is_bound:
            self.initial.update({
                "tour": guest.booked_tour_id,
                "name": guest.full_name,
                "phone_number": guest.contact.phone_number or "",
                "email": guest.contact.email or guest.email or "",
                "adults": guest.adults,
                "children": guest.children,
                "special_requests": guest.special_requests,
                "language": guest.language,
                "tour_notes": guest.tour_notes,
            })

    def clean_name(self):
        value = self.cleaned_data["name"].strip()
        if not value:
            raise forms.ValidationError("Enter the guest's name.")
        return value

    def clean_phone_number(self):
        value = self.cleaned_data["phone_number"].strip()
        if value.startswith("whatsapp:"):
            value = value.removeprefix("whatsapp:")
        return value or None

    def clean(self):
        cleaned = super().clean()
        if self.guest and cleaned.get("tour") and cleaned["tour"].pk != self.guest.booked_tour_id:
            if Guest.objects.filter(rescheduled_from=self.guest).exists():
                self.add_error("tour", "This reservation has already been moved. Edit its replacement booking instead.")
        phone = cleaned.get("phone_number")
        email = cleaned.get("email")
        contacts = Contact.objects.all()
        if getattr(self, "guest", None):
            contacts = contacts.exclude(pk=self.guest.contact_id)
        phone_contact = contacts.filter(phone_number=phone).first() if phone else None
        email_contact = contacts.filter(email=email).first() if email else None
        if self.guest:
            if phone_contact:
                self.add_error("phone_number", "That phone number belongs to another contact.")
            if email_contact:
                self.add_error("email", "That email address belongs to another contact.")
        if phone_contact and email_contact and phone_contact.pk != email_contact.pk:
            raise forms.ValidationError("That phone number and email belong to different guests.")
        return cleaned


class ManualBookingForm(BookingForm):
    pass
