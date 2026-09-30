"""Production settings: everything dev.py leaves open is closed here.

    DJANGO_SETTINGS_MODULE=config.settings.prod

Required: DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS. Expects to sit behind a TLS
reverse proxy that sets X-Forwarded-Proto (and NUM_PROXIES for the throttle).
"""
import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import MIDDLEWARE, REST_FRAMEWORK, SECRET_KEY, _DEV_SECRET_KEY, _env_bool

DEBUG = False

if SECRET_KEY.startswith(_DEV_SECRET_KEY) or len(SECRET_KEY) < 40:
    raise ImproperlyConfigured("set DJANGO_SECRET_KEY to a long random value")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("set DJANGO_ALLOWED_HOSTS to the API's host name(s)")

# TLS terminates at the proxy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _env_bool("SECURE_SSL_REDIRECT", True)
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", str(60 * 60 * 24 * 30)))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
# The API is never meant to be framed.
MIDDLEWARE = [*MIDDLEWARE, "django.middleware.clickjacking.XFrameOptionsMiddleware"]
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Behind exactly one proxy unless told otherwise, so the login throttle keys
# on the real client address rather than the proxy's.
REST_FRAMEWORK = {**REST_FRAMEWORK, "NUM_PROXIES": int(os.environ.get("NUM_PROXIES", "1"))}
# No browsable API or schema UI chrome in production responses.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = ["rest_framework.renderers.JSONRenderer"]
