from django.contrib import admin

from .models import MessageTemplate


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "body")
    exclude = ("channel",)

    def get_readonly_fields(self, request, obj=None):
        return ("is_active",) if obj and obj.system_key else ()

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.system_key)

    def delete_queryset(self, request, queryset):
        queryset.filter(system_key__isnull=True).delete()
