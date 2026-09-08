from django import forms
from .credentials import parse_token


class CredentialForm(forms.Form):
    token = forms.CharField(label="GuruWalk bearer token", max_length=8192,
                           widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "spellcheck": "false"}))

    def clean_token(self):
        try:
            token, self.account_id = parse_token(self.cleaned_data["token"])
            return token
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from None
