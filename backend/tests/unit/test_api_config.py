from django.conf import settings
from django.urls import resolve

from documents.sse_views import document_events


def test_api_requires_a_session_by_default():
    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
        "rest_framework.permissions.IsAuthenticated"
    ]
    assert settings.CORS_ALLOW_CREDENTIALS is True
    assert not getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False)


def test_liveness_probe_stays_public(client):
    response = client.get("/api/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_celery_default_queue_matches_general_worker():
    assert settings.CELERY_TASK_DEFAULT_QUEUE == "default"


def test_document_events_url_resolves_to_async_sse_view():
    match = resolve(
        "/api/documents/00000000-0000-0000-0000-000000000001/events/"
    )

    assert match.func is document_events
