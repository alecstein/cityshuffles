from .models import Message
from django.utils.functional import SimpleLazyObject


def unread_messages(request):
    if not request.user.is_authenticated:
        return {"nav_unread_count": 0}
    return {
        "nav_unread_count": SimpleLazyObject(
            lambda: Message.objects.filter(direction="in", is_read=False).count()
        )
    }
