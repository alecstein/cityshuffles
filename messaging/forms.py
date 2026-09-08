from django import forms
from .models import Contact, Conversation


class ContactEditForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ("name", "phone_number", "email")
        help_texts = {"phone_number": "Format: +[country code][phone number], e.g. +12125551234"}

    def clean_phone_number(self):
        return (self.cleaned_data.get("phone_number") or "").strip() or None

    def clean_email(self):
        value = (self.cleaned_data.get("email") or "").strip() or None
        from bookings.models import Guest
        if value and Guest.objects.exclude(contact=self.instance).filter(email=value).exists():
            raise forms.ValidationError("This email belongs to another guest.")
        return value

    def save(self, commit=True):
        from django.db import transaction
        from bookings.models import Guest
        with transaction.atomic():
            contact = super().save(commit=commit)
            if commit:
                first, _, last = contact.name.partition(" ")
                Guest.objects.filter(contact=contact).update(
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


class NewConversationForm(forms.Form):
    name = forms.CharField(max_length=200)
    phone_number = forms.CharField(
        max_length=40,
        required=False,
        help_text="Format: +[country code][phone number], e.g. +12125551234",
    )
    email = forms.EmailField(required=False)
    channel = forms.ChoiceField(choices=Conversation.Channel.choices)

    def clean_phone_number(self):
        value = self.cleaned_data["phone_number"].strip()
        if value.startswith("whatsapp:"):
            value = value.removeprefix("whatsapp:")
        return value

    def clean(self):
        cleaned_data = super().clean()
        channel = cleaned_data.get("channel")
        phone_number = cleaned_data.get("phone_number")
        email = cleaned_data.get("email")

        if channel in {
            Conversation.Channel.SMS,
            Conversation.Channel.WHATSAPP,
        } and not phone_number:
            self.add_error("phone_number", "A phone number is required for this channel.")

        if channel == Conversation.Channel.EMAIL and not email:
            self.add_error("email", "An email address is required for this channel.")

        if not phone_number and not email:
            raise forms.ValidationError("Add a phone number or email address.")

        return cleaned_data
