from django.conf import settings
from django.urls import resolve

from documents.sse_views import document_events


def test_no_auth_api_does_not_import_django_auth(client):
    assert "django.contrib.auth" not in settings.INSTALLED_APPS
    assert settings.REST_FRAMEWORK["UNAUTHENTICATED_USER"] is None

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
