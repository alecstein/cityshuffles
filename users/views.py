from functools import wraps
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from .forms import ProfileForm, AddUserForm, EditUserForm


def admin_required(view):
    @login_required
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return guarded


@login_required
def profile(request):
    form = ProfileForm(request.POST if request.method == "POST" else None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("users:profile")
    return render(request, "users/form.html", {"form": form, "heading": "Your profile", "profile_page": True})


@admin_required
def index(request):
    return render(request, "users/index.html", {"users": get_user_model().objects.order_by("first_name", "last_name", "username")})


@admin_required
def create(request):
    form = AddUserForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "User created.")
        return redirect("users:index")
    return render(request, "users/form.html", {"form": form, "heading": "Add user"})


@admin_required
def edit(request, pk):
    account = get_object_or_404(get_user_model(), pk=pk)
    if account.is_superuser and not request.user.is_superuser:
        raise PermissionDenied
    form = EditUserForm(request.POST if request.method == "POST" else None, instance=account, actor=request.user)
    if request.method == "POST" and form.is_valid():
        account = form.save()
        if account.pk == request.user.pk:
            update_session_auth_hash(request, account)
        messages.success(request, "User updated.")
        return redirect("users:index")
    return render(request, "users/form.html", {"form": form, "heading": f"Edit {account.username}"})
