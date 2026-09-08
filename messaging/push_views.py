import base64
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.middleware.csrf import get_token
from django.views.decorators.http import require_GET, require_POST
from .models import PushDevice, PushDelivery, Message
from .push import configured, valid_endpoint


def public_key():
    if not configured():
        return ""
    key = serialization.load_pem_private_key(settings.WEB_PUSH_PRIVATE_KEY.read_bytes(), password=None)
    return base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode().rstrip("=")


@login_required
@require_GET
def config(request):
    return JsonResponse({"publicKey": public_key(), "csrf": get_token(request), "account": str(request.user.pk)})


@login_required
@require_POST
def device(request):
    try:
        if len(request.body) > 8192:
            raise ValueError()
        data = json.loads(request.body)
        endpoint = data["endpoint"]
        if not isinstance(endpoint, str) or len(endpoint) > 2048 or not valid_endpoint(endpoint):
            raise ValueError()
        action = data.get("action", "subscribe")
        owned = PushDevice.objects.filter(endpoint=endpoint, user=request.user, session_key=request.session.session_key)
        if action == "remove":
            owned.delete()
            return JsonResponse({"ok": True})
        if action == "test":
            target = owned.first()
            if not target or not configured():
                return JsonResponse({"error": "Enable notifications first."}, status=400)
            if not PushDelivery.objects.filter(device=target, message=None, state__in=["pending", "sending"]).exists():
                PushDelivery.objects.create(device=target)
            return JsonResponse({"ok": True})
        if action != "subscribe" or not configured():
            return JsonResponse({"error": "Notifications have not been configured on the server."}, status=400)
        keys = data["keys"]
        def decode(value):
            if not isinstance(value, str) or len(value) > 128:
                raise ValueError()
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decode(keys["p256dh"]))
        if len(decode(keys["auth"])) != 16:
            raise ValueError()
        if PushDevice.objects.filter(user=request.user).exclude(endpoint=endpoint).count() >= 20:
            return JsonResponse({"error": "Device limit reached."}, status=400)
        PushDevice.objects.update_or_create(endpoint=endpoint, defaults={"user": request.user, "session_key": request.session.session_key, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}, "previews": data.get("previews") is True})
        return JsonResponse({"ok": True})
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"error": "Invalid notification subscription."}, status=400)


@login_required
@require_GET
def unread(request):
    qs = Message.objects.filter(direction="in", is_read=False)
    return JsonResponse({"count": qs.count(), "ids": list(qs.order_by("-pk").values_list("pk", flat=True)[:500])})


@require_GET
def service_worker(request):
    response = HttpResponse((settings.BASE_DIR / "static/js/push-worker.js").read_text(), content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    return response


@require_GET
def manifest(request):
    return JsonResponse({"id": "/", "name": "CityShuffles", "short_name": "CityShuffles", "start_url": "/messages/", "scope": "/", "display": "standalone", "background_color": "#f5f5f2", "theme_color": "#20342f", "icons": [{"src": "/static/push-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]}, content_type="application/manifest+json")
