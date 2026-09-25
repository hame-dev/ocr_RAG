import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("ocrrag")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    # Keeps the engine catalog warm so the UI never waits on a cold probe.
    "probe-engine-health": {
        "task": "ocr.tasks.probe_engine_health",
        "schedule": 60.0,
    },
    "delete-stale-chat-attachments": {
        "task": "chat.tasks.delete_stale_attachments",
        "schedule": 3600.0,
    },
}
