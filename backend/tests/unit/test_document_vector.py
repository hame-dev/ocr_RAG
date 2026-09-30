"""Document-level vectors, for "which documents are about X" questions."""
from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync

from common import fsm
from documents.models import Document
from enrichment.models import MetadataRecord

DIMS = 1024


def _axis(i: int) -> list[float]:
    vec = [0.0] * DIMS
    vec[i] = 1.0
    return vec


class _TopicEmbedder:
    """Embeds by topic word, so similarity is predictable: lease→axis 0, invoice→1, else 2."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        out = []
        for text in texts:
            lower = text.lower()
            out.append(_axis(0) if "lease" in lower or "إيجار" in lower
                       else _axis(1) if "invoice" in lower else _axis(2))
        return out


@pytest.fixture
def make_doc(user, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)

    def make(title: str, summary: str, keywords: list[str] | None = None) -> Document:
        doc = Document.objects.create(
            owner=user, title=title, original_filename=f"{title}.pdf", mime_type="application/pdf",
            storage_path=str(tmp_path / f"{title}.pdf"), status=fsm.READY,
        )
        MetadataRecord.objects.create(document=doc, title=title, summary_short=summary, keywords=keywords or [])
        return doc

    return make


@pytest.mark.django_db
def test_vector_is_created_and_only_refreshed_when_its_text_changes(make_doc):
    from rag.models import DocumentVector
    from rag.tasks import upsert_document_vector

    doc = make_doc("Office lease", "A one-year office lease.", ["rent"])
    embedder = _TopicEmbedder()

    assert upsert_document_vector(doc, doc.metadata, embedder) is True
    row = DocumentVector.objects.get(document=doc)
    assert list(row.embedding)[0] == pytest.approx(1.0)
    assert "Office lease" in embedder.calls[0][0] and "rent" in embedder.calls[0][0]

    assert upsert_document_vector(doc, doc.metadata, embedder) is False
    assert len(embedder.calls) == 1

    doc.metadata.summary_short = "Invoice for services."
    doc.metadata.title = "Services invoice"
    doc.metadata.save()
    assert upsert_document_vector(doc, doc.metadata, embedder) is True
    row.refresh_from_db()
    assert list(row.embedding)[1] == pytest.approx(1.0)


@pytest.mark.django_db
def test_no_metadata_record_means_no_vector(user, tmp_path):
    from rag.models import DocumentVector
    from rag.tasks import upsert_document_vector

    doc = Document.objects.create(
        owner=user, title="Bare", original_filename="bare.pdf", mime_type="application/pdf",
        storage_path=str(tmp_path / "bare.pdf"), status=fsm.READY,
    )
    assert upsert_document_vector(doc, None, _TopicEmbedder()) is False
    assert not DocumentVector.objects.exists()


def _list(**kwargs):
    from chat.tools import list_documents

    return async_to_sync(list_documents.coroutine)(**kwargs)


@pytest.mark.django_db
def test_list_documents_ranks_semantically_and_across_languages(make_doc, monkeypatch):
    from rag.tasks import upsert_document_vector

    embedder = _TopicEmbedder()
    lease = make_doc("عقد إيجار مكتب", "عقد إيجار لمدة سنة.")
    invoice = make_doc("Services invoice", "Invoice for consulting.")
    other = make_doc("Board minutes", "Minutes of the March meeting.")
    for doc in (lease, invoice, other):
        upsert_document_vector(doc, doc.metadata, embedder)
    monkeypatch.setattr("chat.tools.get_client", lambda: embedder)
    ids = [str(d.id) for d in (lease, invoice, other)]

    # An English query finds the Arabic lease; unrelated documents are not listed.
    result = _list(allowed_doc_ids=ids, query="lease agreements", limit=2)
    assert [r["document_id"] for r in result] == [str(lease.id)]
    assert result[0]["similarity"] == 1.0

    # Scoped: a document outside the allowed list never appears.
    result = _list(allowed_doc_ids=[str(invoice.id), str(other.id)], query="lease agreements")
    assert result == []


@pytest.mark.django_db
def test_title_matches_still_listed_when_embedding_fails(make_doc, monkeypatch):
    class Down:
        def embed(self, texts):
            raise RuntimeError("ollama down")

    invoice = make_doc("Services invoice", "Invoice for consulting.")
    make_doc("Board minutes", "Minutes.")
    monkeypatch.setattr("chat.tools.get_client", lambda: Down())
    result = _list(allowed_doc_ids=[str(d.id) for d in Document.objects.all()], query="invoice")
    assert [r["document_id"] for r in result] == [str(invoice.id)]


@pytest.mark.django_db
def test_indexing_creates_the_document_vector(make_doc, monkeypatch, settings):
    from documents.models import TextRevision
    from rag import chunking
    from rag.models import DocumentVector
    from rag.tasks import index_document

    settings.CHUNK_CONTEXT_ENABLED = False
    monkeypatch.setattr(chunking, "get_tokenizer", lambda: None)
    embedder = _TopicEmbedder()
    monkeypatch.setattr("rag.tasks.get_client", lambda: embedder)
    doc = make_doc("Office lease", "A lease.")
    revision = TextRevision.objects.create(
        document=doc, revision_no=1, source="manual_entry", text="The annual rent is due monthly. " * 20,
    )
    doc.current_revision = revision
    doc.save()

    index_document(str(doc.id))
    assert DocumentVector.objects.filter(document=doc).exists()


@pytest.mark.django_db
def test_exact_matches_come_first_and_unrelated_documents_are_not_listed(make_doc, monkeypatch):
    from rag.tasks import upsert_document_vector

    embedder = _TopicEmbedder()
    leases = [make_doc(f"Lease {i}", "A lease.") for i in range(4)]
    minutes = make_doc("Board minutes", "Minutes of the March meeting.")
    # Matches "invoice" only by keyword and has no vector at all (indexed
    # before vectors existed).
    unvectored = make_doc("Supplier bill", "A bill.", ["invoice"])
    for doc in leases + [minutes]:
        upsert_document_vector(doc, doc.metadata, embedder)
    monkeypatch.setattr("chat.tools.get_client", lambda: embedder)
    ids = [str(d.id) for d in Document.objects.all()]

    # More semantic matches than the limit must not push out an exact match.
    result = _list(allowed_doc_ids=ids, query="Lease 3", limit=2)
    assert result[0]["document_id"] == str(leases[3].id)
    assert len(result) == 2

    result = _list(allowed_doc_ids=ids, query="invoice", limit=2)
    assert [r["document_id"] for r in result] == [str(unvectored.id)]

    # Nothing is about "lease" in the minutes, so they are not listed at all.
    result = _list(allowed_doc_ids=ids, query="lease terms", limit=10)
    assert str(minutes.id) not in [r["document_id"] for r in result]
    assert {r["document_id"] for r in result} == {str(d.id) for d in leases}
