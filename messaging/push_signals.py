from django.contrib.auth.signals import user_logged_out
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Message, PushDevice, PushDelivery


@receiver(post_save, sender=Message)
def incoming_message(sender, instance, created, raw=False, **kwargs):
    if raw or not created or instance.direction != "in" or instance.is_read:
        return
    # All active accounts currently have access to the shared inbox.
    PushDelivery.objects.bulk_create([
        PushDelivery(device_id=pk, message=instance)
        for pk in PushDevice.objects.filter(user__is_active=True).values_list("pk", flat=True)
    ], ignore_conflicts=True)


@receiver(user_logged_out)
def detach_device(sender, request, user, **kwargs):
    if request and user:
        PushDevice.objects.filter(user=user, session_key=request.session.session_key).delete()
