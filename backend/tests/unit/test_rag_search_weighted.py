"""Stage-1 indexing writes per-chunk metadata; lexical search weighs it."""
from __future__ import annotations

import hashlib

import pytest
from django.db import connection

from common import fsm
from documents.models import Document, TextRevision
from rag import chunking


class _FakeEmbedder:
    def __init__(self):
        self.inputs: list[str] = []

    def embed(self, texts):
        self.inputs.extend(texts)
        return [[0.1] * 1024 for _ in texts]


class _Down:
    def embed(self, texts):
        raise RuntimeError("ollama down")


@pytest.fixture(autouse=True)
def _no_tokenizer(monkeypatch, settings):
    monkeypatch.setattr(chunking, "get_tokenizer", lambda: None)
    settings.CHUNK_CONTEXT_ENABLED = False


@pytest.fixture
def document(user, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    return Document.objects.create(
        owner=user, title="Lease", original_filename="lease.pdf",
        mime_type="application/pdf", storage_path=str(tmp_path / "lease.pdf"),
        status=fsm.READY,
    )


def _revision(document, text):
    revision = TextRevision.objects.create(document=document, revision_no=1, source="manual_entry", text=text)
    document.current_revision = revision
    document.save()
    return revision


@pytest.mark.django_db
def test_indexed_chunks_carry_keywords_context_and_chunk_meta(document, monkeypatch):
    from enrichment.models import MetadataRecord
    from rag.models import Chunk
    from rag.tasks import index_document

    embedder = _FakeEmbedder()
    monkeypatch.setattr("rag.tasks.get_client", lambda: embedder)
    MetadataRecord.objects.create(
        document=document, title="Office lease agreement", summary_short="A one-year office lease in Riyadh.",
    )
    text = "PAYMENT TERMS\n" + "The tenant shall pay the annual rent of 1000 dollars on the first day of each month. " * 12
    _revision(document, text)

    index_document(str(document.id))

    chunk = Chunk.objects.get(document=document)
    assert chunk.title_text == "Office lease agreement"
    assert chunk.context_text == "PAYMENT TERMS."
    assert "rent" in chunk.keywords_text.lower()
    meta = chunk.meta["chunk"]
    assert meta["keywords"] and meta["summary"] == ""
    assert meta["context_version"] == 0
    assert meta["text_sha256"] == hashlib.sha256(chunk.text.encode()).hexdigest()
    # The header is embedded, the stored text stays raw.
    assert embedder.inputs[0].startswith("Document: Office lease agreement. A one-year office lease in Riyadh.")
    assert "Section: PAYMENT TERMS." in embedder.inputs[0]
    assert not chunk.text.startswith("Document:")


def _chunk(document, index, *, text, keywords_text="", context_text=""):
    from rag.models import Chunk

    return Chunk.objects.create(
        document=document, revision=document.current_revision, chunk_index=index,
        text=text, keywords_text=keywords_text, context_text=context_text, title_text=document.title,
        embedding=[0.1] * 1024,
    )


@pytest.mark.django_db
def test_a_keyword_match_outranks_a_body_match(document, monkeypatch):
    from rag.search import hybrid_search

    monkeypatch.setattr("rag.search.get_client", lambda: _Down())
    _revision(document, "x")
    body = _chunk(document, 0, text="General provisions. The indemnity clause is described later on.")
    keyword = _chunk(document, 1, text="General provisions and definitions for the parties.", keywords_text="indemnity")

    hits = hybrid_search("indemnity", top_k=5, doc_ids=[str(document.id)])
    assert [h["chunk_id"] for h in hits] == [keyword.id, body.id]


@pytest.mark.django_db
def test_tsv_is_never_null_when_metadata_columns_are_empty(document):
    _revision(document, "x")
    chunk = _chunk(document, 0, text="plain body text")
    with connection.cursor() as cursor:
        cursor.execute("SELECT tsv IS NULL FROM rag_chunk WHERE id = %s", [chunk.id])
        assert cursor.fetchone()[0] is False


@pytest.mark.django_db
def test_format_hits_exposes_section_context_and_keywords(document, monkeypatch):
    from rag.search import format_hits, hybrid_search

    monkeypatch.setattr("rag.search.get_client", lambda: _Down())
    _revision(document, "x")
    chunk = _chunk(document, 0, text="The deposit is refundable.", keywords_text="deposit; refund",
                   context_text="3. Deposit. Rules for the security deposit.")
    chunk.section_path = "3. Deposit"
    chunk.meta = {"chunk": {"summary": "Rules for the security deposit.", "keywords": ["deposit", "refund"]}}
    chunk.save()

    [hit] = format_hits(hybrid_search("deposit", top_k=5, doc_ids=[str(document.id)]))
    assert hit["section"] == "3. Deposit"
    assert hit["context"] == "Rules for the security deposit."
    assert hit["keywords"] == ["deposit", "refund"]


@pytest.mark.django_db
def test_reindex_all_only_missing_queues_documents_without_chunk_metadata(document, user, tmp_path, monkeypatch):
    from django.core.management import call_command

    from rag.tasks import index_document

    queued: list[str] = []
    monkeypatch.setattr(index_document, "delay", lambda doc_id: queued.append(doc_id))

    _revision(document, "x")
    old = _chunk(document, 0, text="indexed before per-chunk metadata existed")
    fresh = Document.objects.create(
        owner=user, title="Fresh", original_filename="fresh.pdf", mime_type="application/pdf",
        storage_path=str(tmp_path / "fresh.pdf"), status=fsm.READY,
    )
    _revision(fresh, "y")
    new = _chunk(fresh, 0, text="already has metadata")
    new.meta = {"chunk": {"keywords": []}}
    new.save()

    call_command("reindex_all", "--only-missing")
    assert queued == [str(document.id)]

    queued.clear()
    call_command("reindex_all")
    assert sorted(queued) == sorted([str(document.id), str(fresh.id)])
    assert old.document_id == document.id
