"""One user must never be able to see or act on another user's data.

Another user's resource is always a 404 (never 403), so the API does not even
confirm that it exists.
"""
from __future__ import annotations

import json

import pytest

from chat.models import Conversation
from correction.models import AICorrectionJob
from documents.models import Document, TextRevision
from ocr.models import OCRBatch

pytestmark = pytest.mark.django_db


def _document(owner, title="Lease", status="ready") -> Document:
    return Document.objects.create(
        owner=owner,
        title=title,
        original_filename=f"{title}.pdf",
        mime_type="application/pdf",
        storage_path=f"/tmp/{title}.pdf",
        status=status,
    )


@pytest.fixture
def alices_document(user):
    document = _document(user, title="Alice lease")
    revision = TextRevision.objects.create(
        document=document, revision_no=1, source="manual_entry", text="page one"
    )
    document.current_revision = revision
    document.save(update_fields=["current_revision"])
    return document


def test_document_list_only_contains_own_documents(auth_client, other_client, alices_document, other_user):
    _document(other_user, title="Bob invoice")

    alice_titles = [d["title"] for d in auth_client.get("/api/documents/").json()["results"]]
    bob_titles = [d["title"] for d in other_client.get("/api/documents/").json()["results"]]

    assert alice_titles == ["Alice lease"]
    assert bob_titles == ["Bob invoice"]


def test_upload_is_owned_by_the_uploader(auth_client, user, tmp_path, settings, monkeypatch):
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = str(tmp_path)
    monkeypatch.setattr("ocr.tasks.preprocess_document.delay", lambda *a, **k: None)

    response = auth_client.post(
        "/api/documents/",
        data={"file": SimpleUploadedFile("scan.pdf", b"%PDF-1.4", content_type="application/pdf")},
    )

    assert response.status_code == 201, response.content
    assert Document.objects.get(id=response.json()["id"]).owner == user


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/documents/{doc}/"),
        ("delete", "/api/documents/{doc}/"),
        ("get", "/api/documents/{doc}/pages/1/image/"),
        ("get", "/api/documents/{doc}/revisions/"),
        ("post", "/api/documents/{doc}/ocr/"),
        ("post", "/api/documents/{doc}/ai-correct/"),
        ("get", "/api/documents/{doc}/extraction-plan/"),
        ("post", "/api/documents/{doc}/enrich/"),
        ("get", "/api/documents/{doc}/metadata/"),
        ("post", "/api/documents/{doc}/index/"),
        ("get", "/api/documents/{doc}/events/"),
    ],
)
def test_other_users_document_is_not_found(other_client, alices_document, method, path):
    response = getattr(other_client, method)(
        path.format(doc=alices_document.id),
        data=json.dumps({}) if method == "post" else None,
        content_type="application/json",
    )

    assert response.status_code == 404
    assert Document.objects.filter(id=alices_document.id).exists()


def test_other_users_ocr_batch_and_correction_job_are_not_found(other_client, alices_document):
    batch = OCRBatch.objects.create(document=alices_document, requested_engines=["tesseract"])
    job = AICorrectionJob.objects.create(
        document=alices_document, base_revision=alices_document.current_revision
    )

    assert other_client.get(f"/api/ocr/batches/{batch.id}/").status_code == 404
    assert other_client.get("/api/ocr/batches/").json()["results"] == []
    assert other_client.get(f"/api/ai-corrections/{job.id}/").status_code == 404
    assert other_client.post(
        f"/api/ai-corrections/{job.id}/apply/", data="{}", content_type="application/json"
    ).status_code == 404


def test_conversations_are_private(auth_client, other_client, user):
    conversation = Conversation.objects.create(owner=user, title="mine")

    assert [c["id"] for c in auth_client.get("/api/conversations/").json()["results"]] == [
        str(conversation.id)
    ]
    assert other_client.get("/api/conversations/").json()["results"] == []
    assert other_client.get(f"/api/conversations/{conversation.id}/").status_code == 404
    assert other_client.delete(f"/api/conversations/{conversation.id}/").status_code == 404
    stream = other_client.post(
        f"/api/conversations/{conversation.id}/stream/",
        data=json.dumps({"content": "hi"}),
        content_type="application/json",
    )
    assert stream.status_code == 404


def test_new_conversation_is_owned_by_creator(other_client, other_user):
    response = other_client.post(
        "/api/conversations/", data=json.dumps({"scope": "all"}), content_type="application/json"
    )

    assert response.status_code == 201
    assert Conversation.objects.get(id=response.json()["id"]).owner == other_user


def test_cannot_scope_a_conversation_to_another_users_document(other_client, alices_document):
    response = other_client.post(
        "/api/conversations/",
        data=json.dumps({"scope": "selected", "document_ids": [str(alices_document.id)]}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert not Conversation.objects.exists()


def test_search_is_restricted_to_own_documents(other_client, alices_document, other_user, monkeypatch):
    bobs = _document(other_user, title="Bob invoice")
    captured = {}

    def fake_search(query, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("rag.views.hybrid_search", fake_search)

    unscoped = other_client.post(
        "/api/search/", data=json.dumps({"query": "lease"}), content_type="application/json"
    )
    assert unscoped.status_code == 200
    assert captured["doc_ids"] == [str(bobs.id)]

    # Asking for Alice's document explicitly narrows to nothing, not to hers.
    other_client.post(
        "/api/search/",
        data=json.dumps({"query": "lease", "doc_ids": [str(alices_document.id)]}),
        content_type="application/json",
    )
    assert captured["doc_ids"] == []


def test_empty_scope_never_widens_to_the_whole_corpus(monkeypatch):
    from rag.search import hybrid_search

    def must_not_embed(*args, **kwargs):
        raise AssertionError("an empty scope must return before touching the index")

    monkeypatch.setattr("rag.search.get_client", must_not_embed)

    assert hybrid_search("lease", doc_ids=[]) == []
