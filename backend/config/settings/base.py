"""Base Django settings shared by dev/test."""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "common",
    "documents",
    "ocr",
    "correction",
    "enrichment",
    "rag",
    "chat",
]

# No auth app and no sessions: this system has no login by design.
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
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
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/data/media")

# ---- DRF --------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    # DRF otherwise imports django.contrib.auth.models.AnonymousUser for every
    # request.  The auth app is deliberately absent because this deployment has
    # no login, so represent unauthenticated callers as None instead.
    "UNAUTHENTICATED_USER": None,
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
}

# No auth means no cookies to protect; the browser must reach Django directly
# so that SSE is not buffered by a proxy.
CORS_ALLOW_ALL_ORIGINS = True

# ---- Redis / Celery ---------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
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
