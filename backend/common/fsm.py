"""Document lifecycle state machine.

`transition_to` is the ONLY path allowed to change Document.status. It validates
the transition, records a DocumentEvent (whose auto-increment `seq` doubles as
the SSE Last-Event-ID), and publishes to Redis. Anything that sets `status`
directly bypasses the event stream and the UI silently stops updating.
"""
from __future__ import annotations

import json
import logging

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

UPLOADED = "uploaded"
PREPROCESSING = "preprocessing"
PREPROCESSED = "preprocessed"
OCR_RUNNING = "ocr_running"
OCR_PARTIAL = "ocr_partial"
OCR_DONE = "ocr_done"
OCR_FAILED = "ocr_failed"
TEXT_FINALIZED = "text_finalized"
ENRICHING = "enriching"
ENRICHED = "enriched"
INDEXING = "indexing"
INDEXED = "indexed"
READY = "ready"
FAILED = "failed"

TRANSITIONS: dict[str, set[str]] = {
    UPLOADED: {PREPROCESSING, FAILED},
    PREPROCESSING: {PREPROCESSED, FAILED},
    # A digital PDF can skip OCR entirely and go straight to text.
    PREPROCESSED: {OCR_RUNNING, TEXT_FINALIZED, FAILED},
    OCR_RUNNING: {OCR_PARTIAL, OCR_DONE, OCR_FAILED},
    OCR_PARTIAL: {OCR_RUNNING, OCR_DONE, OCR_FAILED},
    OCR_DONE: {OCR_RUNNING, TEXT_FINALIZED},
    # Even total OCR failure is recoverable: the user can type the text.
    OCR_FAILED: {OCR_RUNNING, TEXT_FINALIZED, FAILED},
    TEXT_FINALIZED: {ENRICHING, OCR_RUNNING, TEXT_FINALIZED},
    # Enrichment failure returns to text_finalized; it never dead-ends.
    ENRICHING: {ENRICHED, TEXT_FINALIZED},
    ENRICHED: {INDEXING, TEXT_FINALIZED, ENRICHING},
    INDEXING: {INDEXED, ENRICHED},
    INDEXED: {READY},
    # From ready every stage can be re-run.
    READY: {OCR_RUNNING, TEXT_FINALIZED, ENRICHING, INDEXING},
    FAILED: {UPLOADED, PREPROCESSING},
}

TERMINAL_OK = {READY}

_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def channel(document_id) -> str:
    return f"doc:{document_id}"


def publish(document_id, kind: str, payload: dict, seq: int | None = None) -> None:
    """Publish a document event to the SSE fan-out. Never raises."""
    try:
        get_redis().publish(
            channel(document_id),
            json.dumps({"kind": kind, "payload": payload, "seq": seq}, default=str),
        )
    except Exception:
        # A dead Redis must not take down a Celery task or a request.
        logger.warning("failed to publish %s for %s", kind, document_id, exc_info=True)


class InvalidTransition(Exception):
    pass


def can_transition(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, set())


def transition_to(document, new_status: str, *, actor: str = "system",
                  payload: dict | None = None, detail: str = "",
                  force: bool = False):
    """Move a document to `new_status`, recording and publishing the change.

    Returns the created DocumentEvent. Raises InvalidTransition unless `force`.
    """
    from documents.models import DocumentEvent

    payload = payload or {}
    current = document.status

    if current == new_status:
        # Idempotent re-entry (e.g. a second engine finishing) is not an error,
        # but it should not spam the event log either.
        return None

    if not force and not can_transition(current, new_status):
        raise InvalidTransition(
            f"cannot move document {document.id} from {current!r} to {new_status!r}"
        )

    document.status = new_status
    if detail:
        document.status_detail = detail
    document.save(update_fields=["status", "status_detail", "updated_at"])

    event = DocumentEvent.objects.create(
        document=document,
        kind="status_change",
        from_status=current,
        to_status=new_status,
        actor=actor,
        payload=payload,
    )
    publish(
        document.id,
        "status_change",
        {"from": current, "to": new_status, "detail": detail, **payload},
        seq=event.seq,
    )
    logger.info("document %s %s -> %s", document.id, current, new_status)
    return event


def record_event(document, kind: str, payload: dict, actor: str = "system"):
    """Record a non-status event (OCR progress, correction, enrichment)."""
    from documents.models import DocumentEvent

    event = DocumentEvent.objects.create(
        document=document, kind=kind, actor=actor, payload=payload
    )
    publish(document.id, kind, payload, seq=event.seq)
    return event
