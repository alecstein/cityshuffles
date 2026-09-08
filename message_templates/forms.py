from django import forms

from .models import MessageTemplate


class MessageTemplateForm(forms.ModelForm):
    class Meta:
        model = MessageTemplate
        fields = ("name", "body")
        widgets = {
            "body": forms.Textarea(attrs={"rows": 7}),
        }
        help_texts = {
            "body": "Use {guest_name}, {guide_first_name}, {tour_name}, or {booking_time}.",
        }

    def clean(self):
        data = super().clean()
        if not (data.get("body") or "").strip():
            raise forms.ValidationError("Add a message.")
        return data

    def save(self, commit=True):
        template = super().save(commit=False)
        template.channel = "any"
        if template.system_key:
            template.is_active = True
        if commit:
            template.save()
        return template
