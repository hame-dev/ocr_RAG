from __future__ import annotations

import hashlib
import logging
import time

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction

from common import fsm
from common.ollama import OllamaError, get_client
from documents.models import Document
from rag.chunking import chunk_text
from rag.context import build_context_header, build_context_text, embed_input
from rag.contextualize import build_windows, merge_keywords, summarize_window, window_key, window_text
from rag.keywords import extract_keywords, keywords_text
from rag.models import Chunk, ChunkContext, DocumentVector, IndexRun

logger = logging.getLogger(__name__)

EMBED_BATCH = 16
# Bumped when the stage-2 output format changes; 0 means "stage 1 only".
CONTEXT_VERSION = 1
# Keywords in the weight-A lexical column after stage 2 merges its own in.
MAX_CHUNK_KEYWORDS = 12
# A soft time limit re-queues the task to carry on (finished windows are
# cached and skipped); this bounds how many times in a row.
MAX_CONTEXT_CONTINUATIONS = 5
# A transient Ollama error (restart, timeout) re-queues the task with a
# backoff; this bounds how many times before the run is marked failed.
MAX_CONTEXT_RETRIES = 3


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def lock_document(document_id):
    """FOR NO KEY UPDATE on the document row; returns its current revision id.

    Every transaction that rewrites a document's chunks takes this first.
    """
    return (
        Document.objects.select_for_update(no_key=True)
        .filter(id=document_id)
        .values_list("current_revision_id", flat=True)
        .first()
    )


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
            # Same lock, taken first, as stage 2's per-window update: the two
            # writers queue instead of deadlocking on chunk rows. NO KEY, so
            # it does not conflict with the FK check of the inserted chunks.
            lock_document(document.id)
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

    # Stage 2 in the background; the document is already searchable. Outside
    # the try above: a broker error here must not undo a successful index.
    try:
        queue_contextualize(str(document.id), run.id)
    except Exception as exc:
        logger.exception("could not queue stage-2 context for %s", document_id)
        IndexRun.objects.filter(id=run.id).update(context_status="failed", context_error=str(exc)[:1000])
    return {"chunks": len(rows), "duration_ms": run.duration_ms}


# ---- stage 2: background contextualization -------------------------------------


def queue_contextualize(document_id: str, index_run_id: int | None = None, *, force: bool = False) -> bool:
    """Queue stage 2 on the low-priority llm_bg queue. False if not queued.

    `force` ignores CHUNK_CONTEXT_ENABLED (an explicit `reindex_all
    --contextualize-only`).
    """
    if not (force or settings.CHUNK_CONTEXT_ENABLED):
        return False
    runs = IndexRun.objects.filter(document_id=document_id, status="succeeded")
    run = runs.filter(id=index_run_id).first() if index_run_id else runs.first()
    if run is None:
        return False
    IndexRun.objects.filter(id=run.id).update(context_status="queued", context_error="")
    contextualize_document.apply_async(args=[str(document_id), run.id], queue="llm_bg")
    return True


def context_model(client) -> str:
    """CHUNK_CONTEXT_MODEL if the host has it, else the main LLM.

    A failed check (Ollama unreachable, /api/tags timing out under load)
    raises: silently switching a document to the big model would be slower and
    would cache its summaries under a different key.
    """
    model = settings.CHUNK_CONTEXT_MODEL
    if model and model != settings.LLM_MODEL:
        if client.has_model(model):
            return model
        logger.warning("%s is not pulled; stage-2 context uses %s", model, settings.LLM_MODEL)
    return settings.LLM_MODEL


class _Aborted(Exception):
    """The document changed under us; the remaining windows are moot."""


@shared_task(
    name="rag.tasks.contextualize_document",
    soft_time_limit=settings.CHUNK_CONTEXT_SOFT_TIME_LIMIT_S,
)
def contextualize_document(document_id: str, index_run_id: int, continuation: int = 0, retries: int = 0):
    """Section summaries + keywords per window of chunks, applied IN PLACE.

    Never changes the document's status: stage 1 already made it READY. Each
    window commits on its own, after re-checking that the document still has
    the revision these chunks were built from and that every chunk still
    exists; otherwise the run stops with no further writes.

    Every window ends up in the ChunkContext cache, including one the model
    could not summarize (stored with an empty summary, so the chunks keep
    their stage-1 data), which is what guarantees a re-queued task makes
    progress instead of repeating the same calls.
    """
    run = IndexRun.objects.filter(id=index_run_id, document_id=document_id).first()
    document = Document.objects.select_related("metadata").filter(id=document_id).first()
    if run is None or document is None or run.status != "succeeded":
        return {"skipped": "no such index run"}
    revision_id = run.revision_id
    if document.current_revision_id != revision_id:
        IndexRun.objects.filter(id=run.id).update(context_status="aborted", context_error="revision changed")
        return {"aborted": "revision changed"}

    # The embedding is only ever written here, never read: leave it out.
    chunks = list(
        Chunk.objects.filter(document=document, revision_id=revision_id)
        .defer("embedding")
        .order_by("chunk_index")
    )
    if not chunks:
        IndexRun.objects.filter(id=run.id).update(context_status="aborted", context_error="no chunks")
        return {"aborted": "no chunks"}

    record = getattr(document, "metadata", None)
    title = document_title(document, record)
    doc_summary = record.summary_short if record else ""
    language = record.primary_language if record else ""
    version = settings.CHUNK_CONTEXT_PROMPT_VERSION
    windows = build_windows(
        chunks,
        max_tokens=settings.CHUNK_CONTEXT_WINDOW_TOKENS,
        max_chunks=settings.CHUNK_CONTEXT_WINDOW_CHUNKS,
    )
    run.context_status = "running"
    run.context_windows, run.context_done, run.context_error = len(windows), 0, ""
    run.save(update_fields=["context_status", "context_windows", "context_done", "context_error"])

    llm_calls = 0
    try:
        client = get_client()
        model = context_model(client)
        run.context_model = model
        run.save(update_fields=["context_model"])
        for done, window in enumerate(windows, start=1):
            section = window[0].section_path
            text = window_text(window)
            key = window_key(title, section, text, model, version)
            cached = ChunkContext.objects.filter(text_sha256=key, model_id=model, prompt_version=version).first()
            if cached:
                result = {"summary": cached.summary, "keywords": list(cached.keywords or [])}
            else:
                llm_calls += 1
                result = summarize_window(client, model, title, doc_summary, section, text, language)
                if result is None:
                    # Remembered as a miss so the next run does not pay for it
                    # again; bump CHUNK_CONTEXT_PROMPT_VERSION to retry these.
                    result = {"summary": "", "keywords": []}
                ChunkContext.objects.get_or_create(
                    text_sha256=key, model_id=model, prompt_version=version,
                    defaults={"summary": result["summary"], "keywords": result["keywords"]},
                )
            if result["summary"]:
                _apply_window(client, document_id, revision_id, window, result, model, title, doc_summary)
            run.context_done = done
            run.save(update_fields=["context_done"])
            fsm.publish(document.id, "context_progress", {"done": done, "of": len(windows)})
            if llm_calls >= settings.CHUNK_CONTEXT_WINDOWS_PER_TASK and done < len(windows):
                # Hand the single LLM worker back: with the priority queue
                # order, anything waiting on `llm` runs before this resumes.
                # Finished windows are cache hits with nothing to update.
                run.context_status = "queued"
                run.save(update_fields=["context_status"])
                contextualize_document.apply_async(
                    args=[str(document_id), run.id, continuation, retries], queue="llm_bg"
                )
                return {"continued": done, "of": len(windows), "llm_calls": llm_calls}
        run.context_status = "succeeded"
    except _Aborted as exc:
        run.context_status, run.context_error = "aborted", str(exc)
    except OllamaError as exc:
        # Ollama restarting or overloaded: try again later, with backoff, so
        # a blip during a library-wide pass does not leave documents behind.
        if retries < MAX_CONTEXT_RETRIES:
            run.context_status, run.context_error = "queued", f"retrying after: {exc}"[:1000]
            run.save(update_fields=["context_status", "context_error"])
            contextualize_document.apply_async(
                args=[str(document_id), run.id, continuation, retries + 1],
                queue="llm_bg",
                countdown=60 * 2 ** retries,
            )
            return {"retry": retries + 1, "error": str(exc)[:300]}
        logger.warning("contextualization gave up after %s retries for %s: %s", retries, document_id, exc)
        run.context_status, run.context_error = "failed", str(exc)[:1000]
    except SoftTimeLimitExceeded:
        if continuation < MAX_CONTEXT_CONTINUATIONS:
            run.context_status, run.context_error = "queued", "time limit; continuing"
            run.save(update_fields=["context_status", "context_error"])
            contextualize_document.apply_async(
                args=[str(document_id), run.id, continuation + 1, retries], queue="llm_bg"
            )
            return {"continued": run.context_done, "of": len(windows)}
        run.context_status, run.context_error = "failed", "time limit"
    except Exception as exc:
        logger.exception("contextualization failed for %s", document_id)
        run.context_status, run.context_error = "failed", str(exc)[:1000]
    run.save(update_fields=["context_status", "context_error"])
    fsm.record_event(document, "contextualized", {
        "status": run.context_status, "windows": len(windows), "done": run.context_done, "llm_calls": llm_calls,
    })
    return {"status": run.context_status, "windows": len(windows), "llm_calls": llm_calls}


def _apply_window(client, document_id, revision_id, window, result, model, title, doc_summary) -> int:
    """Re-embed the window's chunks with the richer header; update in place."""
    summary, window_keywords = result["summary"], result["keywords"]
    changed: list[Chunk] = []
    for chunk in window:
        meta = dict(chunk.meta or {})
        own = dict(meta.get("chunk") or {})
        if (
            own.get("summary") == summary
            and own.get("context_keywords") == window_keywords
            and own.get("context_model") == model
            and own.get("context_version") == CONTEXT_VERSION
        ):
            continue
        stage1 = own.get("keywords") or []
        own.update({
            "summary": summary,
            "context_keywords": window_keywords,
            "context_model": model,
            "context_version": CONTEXT_VERSION,
        })
        own.setdefault("text_sha256", sha256(chunk.text))
        meta["chunk"] = own
        chunk.meta = meta
        chunk.keywords_text = keywords_text(merge_keywords(stage1, window_keywords, cap=MAX_CHUNK_KEYWORDS))
        chunk.context_text = build_context_text(chunk.section_path, summary)
        changed.append(chunk)
    if not changed:
        return 0

    vectors = client.embed([
        embed_input(build_context_header(title, doc_summary, c.section_path, summary), c.text) for c in changed
    ])
    for chunk, vector in zip(changed, vectors):
        chunk.embedding = vector

    with transaction.atomic():
        current = lock_document(document_id)
        if current != revision_id:
            raise _Aborted("revision changed")
        updated = Chunk.objects.bulk_update(
            changed, ["embedding", "meta", "keywords_text", "context_text"], batch_size=50
        )
        if updated != len(changed):
            # Re-indexed meanwhile: these chunk ids are gone. Roll back.
            raise _Aborted("chunks were replaced by a re-index")
    return updated
