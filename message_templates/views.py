from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import MessageTemplateForm
from .models import MessageTemplate


@login_required
def index(request):
    templates = list(MessageTemplate.objects.order_by("-is_active", "name", "pk"))
    for template in templates:
        template.edit_form = MessageTemplateForm(instance=template, auto_id=f"template_{template.pk}_%s")
    return render(
        request,
        "message_templates/index.html",
        {"templates": templates, "new_form": MessageTemplateForm()},
    )


@login_required
@require_http_methods(["GET", "POST"])
def create(request):
    form = MessageTemplateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Message template created.")
        return redirect("message_templates:index")
    return render(request, "message_templates/form.html", {"form": form, "heading": "New template"})


@login_required
@require_http_methods(["GET", "POST"])
def edit(request, pk):
    template = get_object_or_404(MessageTemplate, pk=pk)
    form = MessageTemplateForm(request.POST or None, instance=template)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Message template updated.")
        return redirect("message_templates:index")
    return render(
        request,
        "message_templates/form.html",
        {"form": form, "heading": f"Edit {template.name}"},
    )


@login_required
@require_http_methods(["POST"])
def toggle_active(request, pk):
    template = get_object_or_404(MessageTemplate, pk=pk)
    if template.system_key:
        return HttpResponse("Core message templates cannot be disabled.", status=403)
    template.is_active = request.POST.get("is_active") == "on"
    template.save(update_fields=["is_active", "updated_at"])
    if request.headers.get("HX-Request"):
        return HttpResponse(status=204)
    return redirect("message_templates:index")


@login_required
@require_http_methods(["POST"])
def delete(request, pk):
    template = get_object_or_404(MessageTemplate, pk=pk)
    if template.system_key:
        return HttpResponse("Built-in group templates cannot be deleted.", status=403)
    template.delete()
    messages.success(request, "Message template deleted.")
    return redirect("message_templates:index")
