from .base import *  # noqa: F401,F403

DEBUG = False

# Tests run tasks inline so the whole pipeline can be exercised in one process.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False  # mirrors production: task failures do not raise

# Per-process cache: tests must not depend on (or leak throttle counts into) Redis.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# The default PBKDF2 hasher is deliberately slow; tests create many users.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
