from __future__ import annotations

import logging
import time

from celery import shared_task
from django.conf import settings

from common import fsm
from common.ollama import get_client
from documents.models import Document
from rag.chunking import chunk_text, embed_text
from rag.models import Chunk, IndexRun

logger = logging.getLogger(__name__)

EMBED_BATCH = 16


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

        client = get_client()
        rows: list[Chunk] = []

        for start in range(0, len(drafts), EMBED_BATCH):
            batch = drafts[start : start + EMBED_BATCH]
            vectors = client.embed([embed_text(d) for d in batch])
            for draft, vector in zip(batch, vectors):
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
                        meta={**base_meta, **draft.meta},
                        embedding=vector,
                    )
                )
            fsm.publish(
                document.id,
                "indexing_progress",
                {"embedded": min(start + EMBED_BATCH, len(drafts)), "of": len(drafts)},
            )

        # Re-indexing replaces cleanly by revision, so old chunks never linger.
        Chunk.objects.filter(revision=revision).delete()
        Chunk.objects.bulk_create(rows, batch_size=100)

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
