from django.contrib import admin
from .models import Contact, Conversation, Message


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "phone_number",
        "preferred_channel",
        "last_inbound_channel",
        "whatsapp_opted_in",
        "created_at",
    )
    list_filter = ("preferred_channel", "last_inbound_channel", "whatsapp_opted_in")
    search_fields = ("name", "phone_number", "email")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("contact", "channel", "status", "last_message_at")
    list_filter = ("channel", "status")
    search_fields = ("contact__name", "contact__phone_number")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "conversation",
        "direction",
        "source_vendor",
        "short_body",
        "status",
        "created_at",
    )
    list_filter = ("direction", "status", "source_vendor")
    search_fields = (
        "body",
        "provider_sid",
        "conversation__contact__name",
        "conversation__contact__phone_number",
    )

    @admin.display(description="Message")
    def short_body(self, obj):
        return obj.body[:80]
