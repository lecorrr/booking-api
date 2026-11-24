from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

from bookingapi import __version__

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
# Running natively reads .env; in Docker, Compose passes the same values as variables.
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env.bool("DJANGO_DEBUG", default=False)
SECRET_KEY = env.str("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "drf_spectacular",
    "accounts",
    "resources",
    "bookings",
    "webhooks",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "bookingapi.urls"
WSGI_APPLICATION = "bookingapi.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

REDIS_URL = env.str("REDIS_URL", default="redis://127.0.0.1:6379/0")
CACHES = {"default": env.cache("CACHE_URL", default="locmemcache://")}

# Locally reminders are printed by the worker; set MAILER_BACKEND to the SMTP backend and
# the SMTP_* values to really send them.
MAILER_BACKEND = env.str("MAILER_BACKEND", default="django.core.mail.backends.console.EmailBackend")
MAILERS = {"default": {"BACKEND": MAILER_BACKEND}}
if MAILER_BACKEND == "django.core.mail.backends.smtp.EmailBackend":
    MAILERS["default"]["OPTIONS"] = {
        "host": env.str("SMTP_HOST"),
        "port": env.int("SMTP_PORT", default=587),
        "username": env.str("SMTP_USER", default=""),
        "password": env.str("SMTP_PASSWORD", default=""),
        "use_tls": env.bool("SMTP_USE_TLS", default=True),
    }
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="bookings@example.com")

CELERY_BROKER_URL = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 60
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULE = {
    "expire-holds": {"task": "bookings.tasks.expire_holds", "schedule": 60.0},
    "send-reminders": {"task": "bookings.tasks.send_reminders", "schedule": 60.0},
    "requeue-webhooks": {"task": "webhooks.tasks.requeue_lost_deliveries", "schedule": 300.0},
    "purge-idempotency-keys": {
        "task": "bookings.tasks.purge_idempotency_records",
        "schedule": crontab(hour=3, minute=17),
    },
}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticatedOrReadOnly"],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env.str("THROTTLE_ANON", default="60/min"),
        "user": env.str("THROTTLE_USER", default="240/min"),
        "register": env.str("THROTTLE_REGISTER", default="10/hour"),
        "bookings": env.str("THROTTLE_BOOKINGS", default="30/min"),
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Booking API",
    "DESCRIPTION": "Hold, confirm and cancel time slots without double bookings.",
    "VERSION": __version__,
    "SERVE_INCLUDE_SCHEMA": False,
}

# How bookings behave; the defaults suit a small venue.
BOOKING_HOLD_MINUTES = env.int("BOOKING_HOLD_MINUTES", default=10)
BOOKING_CANCEL_NOTICE_HOURS = env.int("BOOKING_CANCEL_NOTICE_HOURS", default=2)
BOOKING_REMINDER_HOURS = env.int("BOOKING_REMINDER_HOURS", default=24)
BOOKING_MAX_DAYS_AHEAD = env.int("BOOKING_MAX_DAYS_AHEAD", default=90)
IDEMPOTENCY_KEEP_HOURS = 24

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Kyiv"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    SESSION_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=True)
    CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env.str("LOG_LEVEL", default="INFO")},
    "loggers": {"httpx": {"level": "WARNING"}},
}
