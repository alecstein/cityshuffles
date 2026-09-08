from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import UserCreationForm

User = get_user_model()


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")


class AddUserForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "first_name", "last_name", "is_staff")
        labels = {"is_staff": "Administrator"}


class EditUserForm(forms.ModelForm):
    new_password = forms.CharField(required=False, widget=forms.PasswordInput, help_text="Leave blank to keep the current password.")

    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name", "is_staff", "is_active")
        labels = {"is_staff": "Administrator", "is_active": "Account active"}

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor

    def clean(self):
        data = super().clean()
        if self.actor.pk == self.instance.pk and (not data.get("is_staff") or not data.get("is_active")):
            raise forms.ValidationError("You cannot remove your own administrator access or deactivate your own account.")
        if self.instance.is_superuser and not data.get("is_staff"):
            raise forms.ValidationError("A superuser must remain an administrator.")
        password = data.get("new_password")
        if password:
            password_validation.validate_password(password, self.instance)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("new_password"):
            user.set_password(self.cleaned_data["new_password"])
        if commit:
            user.save()
        return user
