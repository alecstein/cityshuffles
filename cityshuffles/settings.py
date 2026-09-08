import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "development-only-change-me-before-production",
)
DEBUG = True

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS",
        "127.0.0.1,localhost",
    ).split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "users",
    "photos",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "messaging",
    "bookings",
    "mytours",
    "integrations",
    "message_templates",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "cityshuffles.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "messaging.context_processors.unread_messages",
            ],
        },
    },
]

WSGI_APPLICATION = "cityshuffles.wsgi.application"
ASGI_APPLICATION = "cityshuffles.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_ROOT = BASE_DIR / ".photo-storage"
# Deliberately not exposed as a public /media directory: use album-token routes.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
PHOTO_GALLERY_LINKS = []  # [{"label": "Instagram", "url": "https://..."}]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "login"

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_SMS_FROM = os.environ.get("TWILIO_SMS_FROM", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")
TWILIO_STATUS_CALLBACK_URL = os.environ.get("TWILIO_STATUS_CALLBACK_URL", "")
TWILIO_VALIDATE_WEBHOOKS = os.environ.get("TWILIO_VALIDATE_WEBHOOKS", "1") == "1"

# Local-only secret storage; never render credentials or store them in SQLite.
INTEGRATIONS_SECRET_DIR = BASE_DIR / ".integration-secrets"
GURUWALK_SYNC_DAYS = 30
# Bound provider work while keeping enough parallelism to make a large
# calendar responsive. These are deliberately conservative defaults.
GURUWALK_BOOKING_CONCURRENCY = 4
GURUWALK_REQUEST_TIMEOUT = 20
GURUWALK_SYNC_STARTUP_TIMEOUT = 10
GURUWALK_SYNC_STARTUP_GRACE = 30
GURUWALK_SYNC_PROGRESS_MAX_AGE = 120
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_OAUTH_REDIRECT_URI = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "")
WEB_PUSH_PRIVATE_KEY = INTEGRATIONS_SECRET_DIR / "web-push.pem"
_push_contact_file = INTEGRATIONS_SECRET_DIR / "web-push-subject.txt"
WEB_PUSH_SUBJECT = os.environ.get("WEB_PUSH_SUBJECT", "") or (
    _push_contact_file.read_text().strip() if _push_contact_file.is_file() else ""
)
