"""
Django settings for member_callout_exercise project.
"""

from pathlib import Path
from decouple import config, Csv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
SECRET_KEY = config("SECRET_KEY", default="django-insecure-dev-only")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "callouts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "member_callout_exercise.urls"

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

WSGI_APPLICATION = "member_callout_exercise.wsgi.application"

# Database — PostgreSQL, credentials from environment
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME", default="crewlink"),
        "USER": config("DB_USER", default="crewlink"),
        "PASSWORD": config("DB_PASSWORD", default="crewlink"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# AI Drafting configuration
AI_PROVIDER = config("AI_PROVIDER", default="fake")
GEMINI_API_KEY = config("GEMINI_API_KEY", default="")
GEMINI_MODEL = config("GEMINI_MODEL", default="gemini-2.5-flash")
AI_TIMEOUT_SECONDS = config("AI_TIMEOUT_SECONDS", default=8.0, cast=float)
AI_TOTAL_BUDGET_SECONDS = config("AI_TOTAL_BUDGET_SECONDS", default=10.0, cast=float)
AI_BREAKER_FAILURES = config("AI_BREAKER_FAILURES", default=5, cast=int)
AI_BREAKER_WINDOW_SECONDS = config("AI_BREAKER_WINDOW_SECONDS", default=60, cast=int)
AI_BREAKER_COOLDOWN_SECONDS = config("AI_BREAKER_COOLDOWN_SECONDS", default=60, cast=int)
AI_THROTTLE = config("AI_THROTTLE", default="10/min")

# CORS configuration
from corsheaders.defaults import default_headers

CORS_ALLOWED_ORIGIN_REGEXES = config(
    "CORS_ALLOWED_ORIGIN_REGEXES",
    default=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    cast=lambda v: [x.strip() for x in v.split(",") if x.strip()],
)
CORS_ALLOW_HEADERS = list(default_headers) + [
    "idempotency-key",
]

# Django REST Framework
REST_FRAMEWORK = {
    # Rule 1: every endpoint is closed by default; views must declare permissions explicitly.
    "DEFAULT_PERMISSION_CLASSES": ["callouts.permissions.DenyAll"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_RATES": {
        "ai_draft": AI_THROTTLE,
    },
}

# Redis cache (wired up for Rule 2 / send-path in later sessions)
REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# ── Celery ────────────────────────────────────────────────────────────────────
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

CELERY_BEAT_SCHEDULE = {
    "sweeper": {
        "task": "callouts.tasks.sweeper",
        "schedule": 60.0,
    },
    "closer": {
        "task": "callouts.tasks.closer",
        "schedule": 60.0,
    },
    "retention_cleanup": {
        "task": "callouts.tasks.retention_cleanup",
        "schedule": 86400.0,  # daily
    },
    "invariant_check_beat": {
        "task": "callouts.tasks.invariant_check",
        "schedule": 300.0,  # every 5 minutes
    },
}

# Retention: delete closed-announcement recipient/push_log data after N days.
RETENTION_DAYS = config("RETENTION_DAYS", default=365, cast=int)
