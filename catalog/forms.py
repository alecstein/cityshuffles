from django import forms

from bookings.models import TourProduct


class TourProductForm(forms.ModelForm):
    class Meta:
        model = TourProduct
        fields = ("name", "description")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Add a tour name.")
        return name
