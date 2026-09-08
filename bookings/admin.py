from django.contrib import admin

from .models import Guest, Guide, Tour


@admin.register(Guide)
class GuideAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "is_admin")


@admin.register(Tour)
class TourAdmin(admin.ModelAdmin):
    list_display = ("name", "start_time")


@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    list_display = (
        "first_name",
        "last_name",
        "email",
        "contact",
        "booked_tour",
        "adults",
        "children",
        "original_adults",
        "original_children",
        "feedback",
        "is_manual",
        "welcome_channel",
        "welcome_status",
    )
    list_filter = ("welcome_channel", "welcome_status", "is_manual", "feedback")
    list_select_related = ("contact", "booked_tour")
