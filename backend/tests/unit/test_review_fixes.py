"""Regression tests for the fixes listed in IMPROVEMENTS.md."""
from __future__ import annotations

import json
import os

import pytest
from asgiref.sync import async_to_sync
from langchain_core.messages import AIMessage, HumanMessage

from common import fsm
from documents.models import Document, TextRevision
from ocr.models import OCRBatch, OCRRun


@pytest.fixture
def document(user, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    return Document.objects.create(
        owner=user, title="Lease", original_filename="lease.pdf",
        mime_type="application/pdf", storage_path=str(tmp_path / "lease.pdf"),
        status=fsm.READY,
    )


# ---- login throttle (1.3) ----------------------------------------------------

@pytest.mark.django_db
def test_login_throttle_ignores_a_forged_forwarded_for(client, user):
    codes = [
        client.post(
            "/api/auth/login/",
            data=json.dumps({"username": "alice", "password": "wrong"}),
            content_type="application/json",
            HTTP_X_FORWARDED_FOR=f"10.0.0.{i}",
        ).status_code
        for i in range(8)
    ]
    assert 429 in codes, codes


# ---- re-indexing (1.1) -------------------------------------------------------

class _FakeEmbedder:
    def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


@pytest.mark.django_db
def test_reindex_drops_chunks_of_earlier_revisions(document, monkeypatch):
    from rag.models import Chunk
    from rag.tasks import index_document

    monkeypatch.setattr("rag.tasks.get_client", lambda: _FakeEmbedder())
    monkeypatch.setattr("rag.chunking.get_tokenizer", lambda: None)

    first = TextRevision.objects.create(
        document=document, revision_no=1, source="manual_entry", text="The annual rent is 1000 dollars, paid monthly. " * 20
    )
    document.current_revision = first
    document.save()
    index_document(str(document.id))

    second = TextRevision.objects.create(
        document=document, revision_no=2, source="manual_edit", parent=first,
        text="The annual rent is 1200 dollars, paid monthly. " * 20,
    )
    document.current_revision = second
    document.save()
    index_document(str(document.id))

    revisions = set(Chunk.objects.filter(document=document).values_list("revision_id", flat=True))
    assert revisions == {second.id}


# ---- conversation summary (1.2) ----------------------------------------------

def test_summarization_folds_in_the_previous_summary_and_keeps_tool_pairs(monkeypatch):
    from chat import graph as chat_graph

    prompts = []

    class FakeSummarizer:
        def __init__(self, **_):
            pass

        async def ainvoke(self, messages):
            prompts.append(messages[1].content)
            return AIMessage(content="new summary")

    monkeypatch.setattr("langchain_ollama.ChatOllama", FakeSummarizer)

    from langchain_core.messages import ToolMessage

    messages = []
    for i in range(12):
        messages.append(HumanMessage(content=f"q{i}", id=f"h{i}"))
        messages.append(AIMessage(content=f"a{i}", id=f"a{i}"))
    # Put a tool call / tool result pair right on the 8-from-the-end boundary.
    messages[-9] = AIMessage(
        content="", id="call", tool_calls=[{"name": "search_documents", "args": {}, "id": "t1"}]
    )
    messages[-8] = ToolMessage(content="[]", tool_call_id="t1", id="tool")

    result = async_to_sync(chat_graph.prepare)({"messages": messages, "summary": "old summary"})

    assert result["summary"] == "new summary"
    assert "old summary" in prompts[0]
    removed = {m.id for m in result["messages"]}
    # Neither half of the pair is summarized away on its own.
    assert "tool" not in removed and "call" not in removed


def test_the_chat_stream_input_does_not_reset_the_summary():
    import inspect

    from chat import sse_views

    assert '"summary": ""' not in inspect.getsource(sse_views)


# ---- OCR batch failure and cancel (1.5, 2.11) --------------------------------

@pytest.fixture
def ocr_batch(document):
    document.status = fsm.OCR_RUNNING
    document.save()
    batch = OCRBatch.objects.create(document=document, requested_engines=["tesseract"], status="running")
    OCRRun.objects.create(batch=batch, document=document, engine_name="tesseract", status="queued")
    return batch


@pytest.mark.django_db
def test_preprocess_failure_inside_a_batch_closes_it(document, ocr_batch, monkeypatch):
    from ocr.tasks import preprocess_document

    def boom(_document):
        raise RuntimeError("corrupt file")

    monkeypatch.setattr("documents.services.preprocess.analyze", boom)

    with pytest.raises(RuntimeError):
        preprocess_document(str(document.id), ["neural"], str(ocr_batch.id))

    document.refresh_from_db()
    ocr_batch.refresh_from_db()
    assert document.status == fsm.OCR_FAILED
    assert ocr_batch.status == "failed"
    assert ocr_batch.runs.get().status == "failed"


@pytest.mark.django_db
def test_a_cancelled_run_is_never_started(document, ocr_batch, monkeypatch):
    from ocr.tasks import run_ocr_engine

    ocr_batch.runs.update(status="cancelled")
    monkeypatch.setattr("ocr.engines.registry.get", lambda name: pytest.fail("engine was started"))

    assert run_ocr_engine(str(ocr_batch.id), "tesseract")["status"] == "cancelled"
    assert ocr_batch.runs.get().status == "cancelled"


@pytest.mark.django_db
def test_stale_batches_are_reaped(document, ocr_batch, settings):
    from datetime import timedelta

    from django.utils import timezone

    from ocr.tasks import reap_stale_ocr_batches

    OCRBatch.objects.filter(id=ocr_batch.id).update(
        started_at=timezone.now() - timedelta(seconds=settings.OCR_BATCH_STALE_S + 60)
    )
    assert reap_stale_ocr_batches() == 1
    document.refresh_from_db()
    assert document.status == fsm.OCR_FAILED


# ---- finalize (2.2) ----------------------------------------------------------

@pytest.mark.django_db
def test_finalize_while_ocr_is_running_is_a_conflict_not_a_500(auth_client, document):
    revision = TextRevision.objects.create(
        document=document, revision_no=1, source="manual_entry", text="hello"
    )
    document.status = fsm.OCR_RUNNING
    document.current_revision = revision
    document.save()

    response = auth_client.post(
        f"/api/documents/{document.id}/text/finalize/", data="{}", content_type="application/json"
    )

    assert response.status_code == 409
    revision.refresh_from_db()
    assert revision.is_final is False


# ---- document files (2.1) ----------------------------------------------------

@pytest.mark.django_db
def test_deleting_a_document_removes_its_files(
    auth_client, document, django_capture_on_commit_callbacks
):
    from documents.services.preprocess import doc_dir

    directory = doc_dir(document)
    os.makedirs(directory)
    open(os.path.join(directory, "original.pdf"), "wb").close()

    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.delete(f"/api/documents/{document.id}/")

    assert response.status_code == 204
    assert not os.path.exists(directory)


# ---- attachments (2.7) -------------------------------------------------------

def test_cp1256_text_is_not_decoded_as_utf16():
    from chat.attachments import _process_text

    arabic = "عقد إيجار سنوي"
    data = arabic.encode("cp1256")
    assert len(data) % 2 == 0  # the case utf-16 used to swallow

    assert _process_text(data)["extracted_text"] == arabic


def test_utf16_with_a_bom_is_decoded():
    from chat.attachments import _process_text

    assert _process_text("hello".encode("utf-16"))["extracted_text"] == "hello"


# ---- chat tools and requests (2.x) -------------------------------------------

def test_tools_deny_when_no_document_list_is_given():
    from chat.tools import _document_is_allowed

    assert _document_is_allowed("x", None) is False
    assert _document_is_allowed("x", ["x"]) is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        "[]",
        json.dumps({"content": 5}),
        json.dumps({"content": "hi", "attachment_ids": ["not-a-uuid"]}),
        b"\xff\xfe",
    ],
)
def test_malformed_chat_bodies_are_400(auth_client, user, body):
    from chat.models import Conversation

    conversation = Conversation.objects.create(owner=user)
    response = auth_client.post(
        f"/api/conversations/{conversation.id}/stream/", data=body, content_type="application/json"
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_conversation_list_counts_messages(auth_client, user):
    from chat.models import Conversation, Message

    conversation = Conversation.objects.create(owner=user, title="t")
    Message.objects.create(conversation=conversation, role="user", content="a")
    Message.objects.create(conversation=conversation, role="assistant", content="b")

    rows = auth_client.get("/api/conversations/").json()["results"]
    assert rows[0]["message_count"] == 2
    assert "messages" not in rows[0]


# ---- one chat turn at a time (2.8) -------------------------------------------

def test_a_second_turn_on_the_same_conversation_is_refused():
    import uuid

    from chat.sse_views import _acquire_turn_lock, _release_turn_lock

    conversation_id = uuid.uuid4()

    async def scenario():
        first = await _acquire_turn_lock(conversation_id)
        if first[1] is None:
            pytest.skip("Redis unavailable; the lock degrades to no lock")
        second = await _acquire_turn_lock(conversation_id)
        await _release_turn_lock(first)
        third = await _acquire_turn_lock(conversation_id)
        await _release_turn_lock(third)
        return second, third

    second, third = async_to_sync(scenario)()
    assert second is None
    assert third is not None and third[1] is not None


@pytest.mark.django_db
def test_a_busy_conversation_answers_409(auth_client, user, monkeypatch):
    from chat.models import Conversation

    async def busy(_conversation_id):
        return None

    monkeypatch.setattr("chat.sse_views._acquire_turn_lock", busy)
    conversation = Conversation.objects.create(owner=user)
    response = auth_client.post(
        f"/api/conversations/{conversation.id}/stream/",
        data=json.dumps({"content": "hi"}), content_type="application/json",
    )
    assert response.status_code == 409
