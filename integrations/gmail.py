"""Gmail OAuth adapter for the shared inbox.

Only the long-lived OAuth refresh token is kept in the owner-only integration
secret directory. Short-lived access tokens stay in memory for one operation.
"""
import base64
import hashlib
import json
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from messaging.models import Contact, Conversation, Message

from .credentials import read_gmail_refresh_token, sync_lock
from .models import Connection


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify "
    "https://www.googleapis.com/auth/gmail.send "
    "openid email"
)


class GmailIntegrationError(Exception):
    """A safe, user-facing Gmail error with no credential details."""


class GmailAuthenticationError(GmailIntegrationError):
    pass


def oauth_configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def authorization_url(state, redirect_uri):
    if not oauth_configured():
        raise GmailIntegrationError("Gmail OAuth is not configured on this server.")
    return AUTH_URL + "?" + urlencode({
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })


def _json_request(url, *, method="GET", data=None, access_token=None):
    headers = {"Accept": "application/json", "User-Agent": "CityShuffles/1.0"}
    if access_token:
        headers["Authorization"] = "Bearer " + access_token
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read(2_000_001))
    except HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise GmailAuthenticationError("Google rejected Gmail access. Reconnect Gmail.") from None
        raise GmailIntegrationError("Gmail is temporarily unavailable. Try again shortly.") from None
    except (URLError, OSError, TimeoutError, ValueError, UnicodeError):
        raise GmailIntegrationError("Could not reach Gmail. Try again shortly.") from None


def exchange_code(code, redirect_uri):
    payload = urlencode({
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    response = _json_request(TOKEN_URL, method="POST", data=payload)
    access_token = response.get("access_token")
    refresh_token = response.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        raise GmailAuthenticationError("Google did not return Gmail access. Try connecting again.")
    if refresh_token is not None and (not isinstance(refresh_token, str) or not refresh_token):
        raise GmailAuthenticationError("Google returned an invalid Gmail refresh token.")
    return access_token, refresh_token


def refresh_access_token(refresh_token):
    payload = urlencode({
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    response = _json_request(TOKEN_URL, method="POST", data=payload)
    access_token = response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GmailAuthenticationError("Google did not refresh Gmail access. Reconnect Gmail.")
    return access_token


class GmailClient:
    def __init__(self, access_token):
        self.access_token = access_token

    @classmethod
    def from_connection(cls, connection):
        refresh_token = read_gmail_refresh_token()
        if not oauth_configured() or not refresh_token:
            raise GmailAuthenticationError("Connect Gmail before using the inbox.")
        return cls(refresh_access_token(refresh_token))

    def request(self, path, *, method="GET", payload=None):
        data = None
        url = API_URL + path
        if payload is not None:
            data = json.dumps(payload).encode()
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self.access_token,
            "User-Agent": "CityShuffles/1.0",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read(10_000_001)
                if len(raw) > 10_000_000:
                    raise GmailIntegrationError("Gmail returned too much data.")
                return json.loads(raw or b"{}")
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise GmailAuthenticationError("Google rejected Gmail access. Reconnect Gmail.") from None
            raise GmailIntegrationError("Gmail is temporarily unavailable. Try again shortly.") from None
        except (URLError, OSError, TimeoutError, ValueError, UnicodeError):
            raise GmailIntegrationError("Could not reach Gmail. Try again shortly.") from None

    def profile(self):
        response = self.request("/profile")
        email = response.get("emailAddress")
        if not isinstance(email, str) or "@" not in email:
            raise GmailIntegrationError("Gmail returned an unexpected account profile.")
        return email.lower()

    def send_email(self, recipient, body, subject="Message from CityShuffles"):
        message_id = make_msgid()
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        response = self.request("/messages/send", method="POST", payload={"raw": raw})
        provider_id = response.get("id")
        if not isinstance(provider_id, str) or not provider_id:
            provider_id = "gmail-" + hashlib.sha256(message_id.encode()).hexdigest()[:58]
        return provider_id[:64]

    def sync_inbox(self, limit=100):
        response = self.request("/messages?labelIds=INBOX&q=is%3Aunread&maxResults=" + str(limit))
        imported = 0
        for item in response.get("messages", []):
            message_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(message_id, str):
                continue
            full = self.request(f"/messages/{message_id}?format=raw")
            encoded = full.get("raw")
            if not isinstance(encoded, str):
                continue
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if _store_inbound(message_id, raw):
                imported += 1
            self.request(
                f"/messages/{message_id}/modify",
                method="POST",
                payload={"removeLabelIds": ["UNREAD"]},
            )
        return imported


def check_auth(connection):
    client = GmailClient.from_connection(connection)
    return client.profile()


def send_email(connection, recipient, body):
    return GmailClient.from_connection(connection).send_email(recipient, body)


def _decode_header(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeError, ValueError):
        return value


def _message_body(message):
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() != "text/plain" or part.get_content_disposition() == "attachment":
                continue
            payload = part.get_payload(decode=True)
            if payload is not None:
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace").strip()
        return ""
    payload = message.get_payload(decode=True)
    if payload is None:
        return str(message.get_payload()).strip()
    return payload.decode(message.get_content_charset() or "utf-8", errors="replace").strip()


def _inbound_provider_id(message_id):
    return "gmail-" + hashlib.sha256(message_id.encode()).hexdigest()[:58]


@transaction.atomic
def _store_inbound(message_id, raw):
    parsed = message_from_bytes(raw)
    sender_name, sender_email = parseaddr(parsed.get("From", ""))
    sender_email = sender_email.strip().lower()
    body = _message_body(parsed)
    if not sender_email or not body:
        return False
    provider_id = _inbound_provider_id(message_id)
    if Message.objects.filter(provider_sid=provider_id).exists():
        return False
    contact = Contact.objects.filter(email__iexact=sender_email).first()
    if not contact:
        contact = Contact.objects.create(
            name=_decode_header(sender_name)[:200] or sender_email,
            email=sender_email,
        )
    conversation, _ = Conversation.objects.get_or_create(
        contact=contact,
        channel=Conversation.Channel.EMAIL,
    )
    message = Message.objects.create(
        conversation=conversation,
        direction=Message.Direction.INCOMING,
        body=body,
        provider_sid=provider_id,
        source_vendor="gmail",
        status=Message.Status.RECEIVED,
        is_read=False,
    )
    conversation.last_message_at = message.created_at
    conversation.save(update_fields=["last_message_at"])
    Contact.objects.filter(pk=contact.pk).update(
        last_inbound_channel=Conversation.Channel.EMAIL,
        last_inbound_at=timezone.now(),
    )
    return True


def run_sync():
    """Poll Gmail once for the background integration worker."""
    with sync_lock() as acquired:
        if not acquired:
            return False
        connection, _ = Connection.objects.get_or_create(vendor="gmail")
        if not connection.enabled:
            return False
        now = timezone.now()
        connection.last_attempt_at = now
        connection.sync_status = "running"
        connection.save(update_fields=["last_attempt_at", "sync_status"])
        try:
            client = GmailClient.from_connection(connection)
            client.sync_inbox()
            connection.auth_status = "ok"
            connection.auth_checked_at = timezone.now()
            connection.sync_status = "ok"
            connection.last_sync_at = now
            connection.detail = ""
        except GmailAuthenticationError as exc:
            connection.auth_status = "failed"
            connection.auth_checked_at = timezone.now()
            connection.sync_status = "failed"
            connection.enabled = False
            connection.detail = str(exc)
        except GmailIntegrationError as exc:
            connection.sync_status = "failed"
            connection.detail = str(exc)
        connection.save()
        return connection.sync_status == "ok"
