from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from bookings.models import TourProduct

from .forms import TourProductForm


def _staff_only(user):
    return user.is_staff or user.is_superuser


staff_required = user_passes_test(_staff_only)


@login_required
@staff_required
def index(request):
    products = TourProduct.objects.order_by("name", "pk")
    return render(request, "catalog/index.html", {"products": products})


@login_required
@staff_required
@require_http_methods(["GET", "POST"])
def create(request):
    form = TourProductForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Tour added to the catalog.")
        return redirect("catalog:index")
    return render(request, "catalog/form.html", {"form": form, "heading": "Add tour"})


@login_required
@staff_required
@require_http_methods(["GET", "POST"])
def edit(request, pk):
    product = get_object_or_404(TourProduct, pk=pk)
    form = TourProductForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Tour updated.")
        return redirect("catalog:index")
    return render(
        request,
        "catalog/form.html",
        {"form": form, "heading": f"Edit {product.name}"},
    )
