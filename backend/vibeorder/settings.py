"""VibeOrder Django settings.

One modular monolith with two deployment profiles (VIBEORDER_PROFILE=branch|cloud).
Secrets and database credentials come from the environment, never source control.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")
PROFILE = os.environ.get("VIBEORDER_PROFILE", "branch")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "apps.core",
    "apps.catalog",
    "apps.orders",
    "apps.kitchen",
    "apps.cash",
    "apps.fulfillment",
    "apps.stock",
    "apps.cloud",
    "apps.reports",
    "apps.ops",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.CorrelationIdMiddleware",
    "apps.ops.middleware.HostFenceMiddleware",
]

ROOT_URLCONF = "vibeorder.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "vibeorder.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PGDATABASE", "vibeorder"),
        "USER": os.environ.get("PGUSER", ""),
        "PASSWORD": os.environ.get("PGPASSWORD", ""),
        "HOST": os.environ.get("PGHOST", ""),
        "PORT": os.environ.get("PGPORT", ""),
        "TEST": {"NAME": os.environ.get("PGTESTDATABASE", "vibeorder_test")},
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.core.auth.StaffSessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "EXCEPTION_HANDLER": "apps.core.errors.exception_handler",
    "UNAUTHENTICATED_USER": None,
}

CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOW_HEADERS = [
    "accept", "authorization", "content-type", "idempotency-key",
    "x-device-token", "x-correlation-id",
]

# Operational defaults (overridable per environment).
QUOTE_TTL_SECONDS = int(os.environ.get("VIBEORDER_QUOTE_TTL", "300"))
PAIRING_CODE_TTL_SECONDS = int(os.environ.get("VIBEORDER_PAIRING_TTL", "600"))
STAFF_SESSION_TTL_HOURS = int(os.environ.get("VIBEORDER_SESSION_TTL_HOURS", "14"))
PIN_MAX_ATTEMPTS = int(os.environ.get("VIBEORDER_PIN_MAX_ATTEMPTS", "5"))
PIN_LOCKOUT_SECONDS = int(os.environ.get("VIBEORDER_PIN_LOCKOUT", "300"))
PRINT_SPOOL_DIR = Path(os.environ.get("VIBEORDER_SPOOL_DIR", BASE_DIR / "var" / "spool"))
PRINT_LEASE_SECONDS = int(os.environ.get("VIBEORDER_PRINT_LEASE", "30"))

# Host fencing: only the host whose id matches Branch.writable_host may write (A19).
HOST_ID = os.environ.get("VIBEORDER_HOST_ID", "dev-host")
BACKUP_DIR = Path(os.environ.get("VIBEORDER_BACKUP_DIR", BASE_DIR / "var" / "backups"))
# Fernet key (urlsafe base64, 32 bytes). Dev derives one from SECRET_KEY; production must set it.
BACKUP_KEY = os.environ.get("VIBEORDER_BACKUP_KEY", "")
BACKUP_MAX_AGE_MINUTES = int(os.environ.get("VIBEORDER_BACKUP_MAX_AGE", "15"))

# Cloud / online intake (M4)
CLOUD_URL = os.environ.get("VIBEORDER_CLOUD_URL", "http://127.0.0.1:8000")
CLOUD_BRANCH_TOKEN = os.environ.get("VIBEORDER_CLOUD_TOKEN", "")
OFFER_TIMEOUT_SECONDS = int(os.environ.get("VIBEORDER_OFFER_TIMEOUT", "120"))
HEARTBEAT_STALE_SECONDS = int(os.environ.get("VIBEORDER_HEARTBEAT_STALE", "60"))
SYNC_BATCH_SIZE = int(os.environ.get("VIBEORDER_SYNC_BATCH", "200"))
ONLINE_PAYMENTS_ENABLED = os.environ.get("VIBEORDER_ONLINE_PAYMENTS", "0") == "1"
FAKEPAY_SECRET = os.environ.get("VIBEORDER_FAKEPAY_SECRET", "fakepay-dev-secret")
PAYMENT_WINDOW_SECONDS = int(os.environ.get("VIBEORDER_PAYMENT_WINDOW", "600"))
OWNER_SESSION_TTL_HOURS = int(os.environ.get("VIBEORDER_OWNER_TTL_HOURS", "12"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "loggers": {"vibeorder": {"handlers": ["console"], "level": "INFO"}},
}
