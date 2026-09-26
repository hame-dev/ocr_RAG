"""Base Django settings shared by dev/test."""
import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_DEV_SECRET_KEY = "dev-only-not-secret"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", _DEV_SECRET_KEY)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

# Sessions are signed with SECRET_KEY, so shipping the public dev key would let
# anyone forge a login.
if not DEBUG and SECRET_KEY.startswith(_DEV_SECRET_KEY):
    raise ImproperlyConfigured("set DJANGO_SECRET_KEY when DJANGO_DEBUG is off")


def _env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").lower() in {"1", "true", "yes"}


INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "common",
    "accounts",
    "documents",
    "ocr",
    "correction",
    "enrichment",
    "rag",
    "chat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL", "postgresql://ocrrag:ocrrag@db:5432/ocrrag"),
        conn_max_age=600,
        # Long-lived Celery workers otherwise keep a dead connection after a
        # Postgres restart and fail their next task with AdminShutdown.
        conn_health_checks=True,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

STATIC_URL = "/static/"
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/data/media")
# Document uploads. Matches the frontend's MAX_MB; chat attachments have their own cap.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024

# ---- DRF --------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["accounts.authentication.SessionAuthentication401"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # Only the login endpoint opts into throttling; it is the brute-force target.
    "DEFAULT_THROTTLE_RATES": {"login": os.environ.get("LOGIN_THROTTLE_RATE", "5/min")},
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 50,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "OCR + RAG API",
    "DESCRIPTION": "Arabic/English OCR, correction, metadata enrichment and agentic RAG.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAuthenticated"],
}

# ---- Sessions / CORS / CSRF -------------------------------------------------
# The browser reaches Django directly (not through Next) so SSE is not buffered
# by a proxy. That makes every call cross-origin, so the session cookie is sent
# with credentials and the frontend origin must be listed explicitly.
# Frontend and API must share a site (e.g. localhost:3000 + localhost:8000, or
# app.example.com + api.example.com) for SameSite=Lax cookies to be sent.
CORS_ALLOWED_ORIGINS = _env_list("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:3000")

SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Off by default because local development is plain HTTP. Turn both on behind TLS.
SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE")
CSRF_COOKIE_SECURE = _env_bool("CSRF_COOKIE_SECURE")

# ---- Redis / Celery ---------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
# Shared cache so login throttling counts across every uvicorn worker.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
# Keep unrouted tasks on the queue consumed by the general worker.  Celery's
# built-in default is named "celery", while the compose worker is intentionally
# named "default"; without this, preprocessing and chord callbacks never run.
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "ocr.tasks.run_ocr_engine": {"queue": "ocr_cpu"},
    "ocr.tasks.run_ocr_engine_llm": {"queue": "llm"},
    "correction.tasks.*": {"queue": "llm"},
    "enrichment.tasks.*": {"queue": "llm"},
    "rag.tasks.*": {"queue": "index"},
}

# ---- Ollama -----------------------------------------------------------------
# Runs on the HOST so it can use Metal. Containers reach it via
# host.docker.internal; there is no GPU passthrough on macOS Docker.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.5:9b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
EMBED_DIMS = int(os.environ.get("EMBED_DIMS", "1024"))

# The 262k context these models advertise does not fit in 32GB of KV cache.
LLM_NUM_CTX = int(os.environ.get("LLM_NUM_CTX", "16384"))

# ---- OCR --------------------------------------------------------------------
EASYOCR_URL = os.environ.get("EASYOCR_URL", "http://ocr-easyocr:8081")
PADDLE_URL = os.environ.get("PADDLE_URL", "http://ocr-paddle:8082")
OCR_DEFAULT_TIMEOUT_S = int(os.environ.get("OCR_DEFAULT_TIMEOUT_S", "300"))
ENGINE_HEALTH_TTL_S = 60

# ---- Chandra OCR 2 ----------------------------------------------------------
# Chandra ships no Ollama build, so it is driven through its own CLI
# (`pip install chandra-ocr[hf]`). Two inference methods:
#   hf   — local transformers + torch. Self-contained, slow, needs the weights
#          downloaded once (~9GB) and enough VRAM/unified memory.
#   vllm — talks to a `chandra_vllm` server the operator starts separately.
# `hf` is the default because it needs no second process.
CHANDRA_BIN = os.environ.get("CHANDRA_BIN", "chandra")
CHANDRA_METHOD = os.environ.get("CHANDRA_METHOD", "hf")
DEFAULT_OCR_ENGINE = os.environ.get("DEFAULT_OCR_ENGINE", "chandra_ollama")
# Weights are large; first run downloads them and can far exceed a normal page
# timeout, so this budget is per-run, not per-page.
CHANDRA_TIMEOUT_S = int(os.environ.get("CHANDRA_TIMEOUT_S", "1800"))
# `--method hf` forces batch size 1 in the CLI itself; kept configurable for vllm.
CHANDRA_BATCH_SIZE = int(os.environ.get("CHANDRA_BATCH_SIZE", "0")) or None

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
