from .models import Conversation, Message
from django.db.models import Exists, OuterRef
from django.utils.functional import SimpleLazyObject


def unread_messages(request):
    if not request.user.is_authenticated:
        return {"nav_unread_count": 0}
    def unread_count():
        from bookings.models import Booking
        booked = Booking.objects.filter(contact_id=OuterRef("conversation__contact_id"))
        unread = Message.objects.filter(direction="in", is_read=False).filter(Exists(booked))
        manual = Conversation.objects.filter(marked_unread=True).filter(
            Exists(Booking.objects.filter(contact_id=OuterRef("contact_id")))
        ).exclude(Exists(Message.objects.filter(
            conversation_id=OuterRef("pk"), direction="in", is_read=False,
        )))
        return unread.count() + manual.count()

    return {
        "nav_unread_count": SimpleLazyObject(
            unread_count
        )
    }
