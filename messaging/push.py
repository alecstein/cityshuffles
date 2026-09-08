import json
from datetime import timedelta
from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.sessions.models import Session
from django.db.models import F
from django.utils import timezone
from pywebpush import webpush, WebPushException
from requests.exceptions import RequestException
from django.utils.crypto import constant_time_compare
from .models import PushDevice, PushDelivery


def valid_endpoint(endpoint):
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        return (url.scheme == "https" and not url.username and not url.password and url.port in (None, 443)
                and (host in {"fcm.googleapis.com", "updates.push.services.mozilla.com"}
                     or host.endswith(".push.apple.com") or host.endswith(".notify.windows.com")))
    except (ValueError, TypeError):
        return False


def configured():
    return bool(settings.WEB_PUSH_SUBJECT and settings.WEB_PUSH_PRIVATE_KEY.is_file())


def deliver_pending(limit=50):
    if not configured():
        return
    now = timezone.now()
    # A lease makes interrupted jobs retryable and prevents concurrent workers claiming the same job.
    for pk in list(PushDelivery.objects.filter(state__in=["pending", "sending"], due_at__lte=now).values_list("pk", flat=True)[:limit]):
        if not PushDelivery.objects.filter(pk=pk, state__in=["pending", "sending"], due_at__lte=now).update(state="sending", due_at=now + timedelta(minutes=2), attempts=F("attempts") + 1):
            continue
        job = PushDelivery.objects.select_related("device__user", "message__conversation__contact").filter(pk=pk).first()
        if not job:
            continue
        device = job.device
        session = Session.objects.filter(session_key=device.session_key, expire_date__gt=now).first()
        session_data = session.get_decoded() if session else {}
        valid_hash = any(constant_time_compare(session_data.get("_auth_user_hash", ""), value) for value in [device.user.get_session_auth_hash(), *device.user.get_session_auth_fallback_hash()])
        if not device.user.is_active or not session or str(session_data.get("_auth_user_id")) != str(device.user_id) or not valid_hash:
            device.delete()
            continue
        if job.message and (job.message.is_read or job.message.created_at < now - timedelta(hours=24)):
            PushDelivery.objects.filter(pk=pk).update(state="skipped")
            continue
        message = job.message
        payload = {"title": "CityShuffles", "body": "Notifications are working." if not message else "New guest message", "url": "/messages/", "tag": f"message-{message.pk}" if message else f"test-{pk}"}
        if message:
            payload["url"] += f"?conversation={message.conversation_id}"
            if device.previews:
                payload.update(title=f"{message.conversation.contact.name} · {message.conversation.get_channel_display()}", body=message.body[:160])
        try:
            if not valid_endpoint(device.endpoint):
                raise ValueError("Unsupported push endpoint")
            webpush(subscription_info={"endpoint": device.endpoint, "keys": device.keys}, data=json.dumps(payload), vapid_private_key=str(settings.WEB_PUSH_PRIVATE_KEY), vapid_claims={"sub": settings.WEB_PUSH_SUBJECT}, ttl=300, timeout=10)
            PushDelivery.objects.filter(pk=pk).update(state="accepted", error="")
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                device.delete()
                continue
            retry = (status is None or status == 429 or status >= 500) and job.attempts < 6
            PushDelivery.objects.filter(pk=pk).update(state="pending" if retry else "failed", due_at=now + timedelta(seconds=min(3600, 30 * 2 ** job.attempts)), error=f"Push service status: {status or 'unreachable'}")
        except RequestException:
            PushDelivery.objects.filter(pk=pk).update(state="pending" if job.attempts < 6 else "failed", due_at=now + timedelta(seconds=min(3600, 30 * 2 ** job.attempts)), error="Push service unreachable")
        except Exception:
            PushDelivery.objects.filter(pk=pk).update(state="failed", error="Push configuration or delivery error")
