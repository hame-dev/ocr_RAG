from .base import *  # noqa: F401,F403

DEBUG = False

# Tests run tasks inline so the whole pipeline can be exercised in one process.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False  # mirrors production: task failures do not raise
