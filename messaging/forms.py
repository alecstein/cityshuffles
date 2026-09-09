from django import forms
from .models import Guest


class GuestEditForm(forms.ModelForm):
    class Meta:
        model = Guest
        fields = ("name", "phone_number", "email")
        help_texts = {"phone_number": "Format: +[country code][phone number], e.g. +12125551234"}

    def clean_phone_number(self):
        return (self.cleaned_data.get("phone_number") or "").strip() or None

    def clean_email(self):
        value = (self.cleaned_data.get("email") or "").strip().lower() or None
        from bookings.models import Booking
        if value and (Guest.objects.exclude(pk=self.instance.pk).filter(email__iexact=value).exists()
                      or Booking.objects.exclude(contact=self.instance).filter(email__iexact=value).exists()):
            raise forms.ValidationError("This email belongs to another guest.")
        return value

    def save(self, commit=True):
        from django.db import transaction
        from bookings.models import Booking
        with transaction.atomic():
            contact = super().save(commit=commit)
            if commit:
                first, _, last = contact.name.partition(" ")
                Booking.objects.filter(contact=contact).update(
                    first_name=first, last_name=last, email=contact.email,
                )
        return contact


class MessageForm(forms.Form):
    body = forms.CharField(
        label="",
        max_length=1600,
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "placeholder": "Write a message…",
                "autocomplete": "off",
            }
        ),
    )
