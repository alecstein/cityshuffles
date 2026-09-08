from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Guest
from .services import schedule_welcome_for_guest


@receiver(post_save, sender=Guest)
def welcome_new_guest(sender, instance, created, **kwargs):
    if created and not instance.imported:
        schedule_welcome_for_guest(instance)
