from __future__ import annotations

import hashlib
import logging
import time

from celery import shared_task
from django.conf import settings
from django.db import transaction

from common import fsm
from common.ollama import get_client
from documents.models import Document
from rag.chunking import chunk_text
from rag.context import build_context_header, build_context_text, embed_input
from rag.keywords import extract_keywords, keywords_text
from rag.models import Chunk, DocumentVector, IndexRun

logger = logging.getLogger(__name__)

EMBED_BATCH = 16
# Bumped when the stage-2 output format changes; 0 means "stage 1 only".
CONTEXT_VERSION = 1


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_title(document, record) -> str:
    return (record.title if record and record.title else "") or document.display_title


def chunk_meta(text: str, keywords: list[str]) -> dict:
    """The chunk's own metadata, stored under meta["chunk"]."""
    return {
        "keywords": keywords,
        "summary": "",
        "context_model": "",
        "context_version": 0,
        "text_sha256": sha256(text),
    }


def document_vector_text(document, record) -> str:
    """What the document-level vector embeds: identity, gist and vocabulary."""
    parts = [
        document_title(document, record),
        record.summary_long or record.summary_short,
        ", ".join(record.keywords or []),
        ", ".join(record.topics or []),
    ]
    return "\n".join(p.strip() for p in parts if p and p.strip())


def upsert_document_vector(document, record, client) -> bool:
    """Create or refresh the document's vector. True when it was (re)embedded.

    Skipped without a metadata record (there is nothing beyond the title to
    embed) and when the embedded text is unchanged since the last run.
    """
    if record is None:
        return False
    text = document_vector_text(document, record)
    if not text:
        return False
    digest = sha256(text)
    existing = DocumentVector.objects.filter(document=document).first()
    if existing and existing.text_sha256 == digest and existing.model == settings.EMBED_MODEL:
        return False
    [vector] = client.embed([text])
    DocumentVector.objects.update_or_create(
        document=document,
        defaults={"embedding": vector, "text_sha256": digest, "model": settings.EMBED_MODEL},
    )
    return True


@shared_task(name="rag.tasks.index_document")
def index_document(document_id: str):
    """Chunk, embed and index the finalized revision."""
    document = Document.objects.select_related("current_revision", "metadata").get(
        id=document_id
    )
    revision = document.current_revision
    if not revision or not revision.text.strip():
        return {"error": "no finalized text"}

    started = time.monotonic()
    run = IndexRun.objects.create(
        document=document,
        revision=revision,
        embedding_model=settings.EMBED_MODEL,
        dims=settings.EMBED_DIMS,
        status="running",
    )

    if document.status in (fsm.ENRICHED, fsm.READY, fsm.INDEXED):
        try:
            fsm.transition_to(document, fsm.INDEXING)
        except fsm.InvalidTransition:
            pass

    try:
        drafts = chunk_text(revision.text)
        if not drafts:
            raise ValueError("chunking produced no chunks")

        record = getattr(document, "metadata", None)
        base_meta = record.filter_meta() if record else {}
        title = document_title(document, record)
        summary = record.summary_short if record else ""

        client = get_client()
        rows: list[Chunk] = []

        for start in range(0, len(drafts), EMBED_BATCH):
            batch = drafts[start : start + EMBED_BATCH]
            keywords = [extract_keywords(d.text, d.lang) for d in batch]
            # The header (document + section) is embedded with the text but
            # never stored in `text`, so citations and quotes stay raw.
            vectors = client.embed([
                embed_input(build_context_header(title, summary, d.section_path), d.text)
                for d in batch
            ])
            for draft, vector, kws in zip(batch, vectors, keywords):
                rows.append(
                    Chunk(
                        document=document,
                        revision=revision,
                        chunk_index=draft.chunk_index,
                        page_start=draft.page_start,
                        page_end=draft.page_end,
                        section_path=draft.section_path,
                        lang=draft.lang,
                        text=draft.text,
                        token_count=draft.token_count,
                        char_count=draft.char_count,
                        # Document metadata is denormalized onto every chunk so
                        # filtered search never needs a join.
                        meta={**base_meta, **draft.meta, "chunk": chunk_meta(draft.text, kws)},
                        keywords_text=keywords_text(kws),
                        context_text=build_context_text(draft.section_path, ""),
                        title_text=title,
                        embedding=vector,
                    )
                )
            fsm.publish(
                document.id,
                "indexing_progress",
                {"embedded": min(start + EMBED_BATCH, len(drafts)), "of": len(drafts)},
            )

        # Replace every chunk of the document, not only this revision's: search
        # does not filter by revision, so an earlier revision's chunks would
        # keep surfacing text the user has since corrected. One transaction, so
        # search never sees the document with no chunks at all.
        with transaction.atomic():
            Chunk.objects.filter(document=document).delete()
            Chunk.objects.bulk_create(rows, batch_size=100)

        try:
            upsert_document_vector(document, record, client)
        except Exception:
            # Only cross-document listing degrades without it; never fail the index.
            logger.warning("document vector failed for %s", document_id, exc_info=True)

        run.chunk_count = len(rows)
        run.status = "succeeded"
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.save()

        try:
            fsm.transition_to(document, fsm.INDEXED, payload={"chunks": len(rows)})
            fsm.transition_to(document, fsm.READY)
        except fsm.InvalidTransition:
            pass

        fsm.record_event(document, "indexed", {"chunks": len(rows)})
        return {"chunks": len(rows), "duration_ms": run.duration_ms}

    except Exception as exc:
        logger.exception("indexing failed for %s", document_id)
        run.status = "failed"
        run.error_message = str(exc)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.save()
        try:
            fsm.transition_to(document, fsm.ENRICHED, force=True)
        except Exception:
            pass
        fsm.record_event(document, "index_failed", {"error": str(exc)[:300]})
        return {"error": str(exc)}
