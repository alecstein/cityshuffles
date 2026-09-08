from django import forms
from .credentials import parse_freetour_cookie_header, parse_token


class CredentialForm(forms.Form):
    token = forms.CharField(label="GuruWalk bearer token", max_length=8192,
                           widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "spellcheck": "false"}))

    def clean_token(self):
        try:
            token, self.account_id = parse_token(self.cleaned_data["token"])
            return token
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from None


class FreeTourCredentialForm(forms.Form):
    cookies = forms.CharField(
        label="FreeTour Cookie header",
        max_length=32768,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "spellcheck": "false"}),
    )

    def clean_cookies(self):
        try:
            return parse_freetour_cookie_header(self.cleaned_data["cookies"])
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from None
