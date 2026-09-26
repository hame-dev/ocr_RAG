"""SSE endpoints.

These are plain Django `async def` views, NOT DRF APIViews. DRF's async support
is poor and converting these would break streaming — that is deliberate, please
leave it. DRF handles every other endpoint, running sync in the ASGI threadpool.
"""
from __future__ import annotations

import asyncio
import json
import logging

import redis.asyncio as aioredis
from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse

from common.fsm import channel
from common.ownership import authenticated_user, owned_documents, unauthorized
from common.sse import HEARTBEAT, sse, stream_headers

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 15


async def _snapshot(document_id: str) -> dict:
    """Full current state, always sent first.

    Emitting a snapshot before any live event means the client never has to
    reconcile "what did I miss before I connected" — it just replaces its state.
    """
    from documents.models import Document
    from ocr.models import OCRBatch

    document = await Document.objects.select_related("current_revision").aget(id=document_id)

    batches = []
    async for batch in OCRBatch.objects.filter(document_id=document_id).order_by("-created_at")[:3]:
        runs = []
        async for run in batch.runs.all():
            runs.append(
                {
                    "run_id": str(run.id),
                    "engine": run.engine_name,
                    "status": run.status,
                    "duration_ms": run.duration_ms,
                    "mean_confidence": run.mean_confidence,
                    "gibberish_score": run.gibberish_score,
                    "char_count": run.char_count,
                    "warnings": run.warnings,
                    "error_code": run.error_code,
                    "error_message": run.error_message[:300] if run.error_message else "",
                    "preview": (run.text or "")[:400],
                }
            )
        batches.append(
            {
                "batch_id": str(batch.id),
                "status": batch.status,
                "requested_engines": batch.requested_engines,
                "runs": runs,
            }
        )

    last_seq = 0
    async for event in document.events.order_by("-seq")[:1]:
        last_seq = event.seq

    return {
        "document_id": str(document.id),
        "status": document.status,
        "status_detail": document.status_detail,
        "page_count": document.page_count,
        "is_digital_pdf": document.is_digital_pdf,
        "detected_languages": document.detected_languages,
        "current_revision_no": (
            document.current_revision.revision_no if document.current_revision_id else None
        ),
        "batches": batches,
        "last_seq": last_seq,
    }


async def document_events(request, document_id: str):
    """Live document lifecycle + OCR progress stream."""
    user = await authenticated_user(request)
    if user is None:
        return unauthorized()
    # Checked before the stream opens: a non-200 makes EventSource stop instead
    # of reconnecting forever.
    if not await owned_documents(user).filter(id=document_id).aexists():
        return JsonResponse({"detail": "no such document"}, status=404)

    async def generator():
        client = None
        pubsub = None
        try:
            try:
                yield sse("snapshot", await _snapshot(document_id))
            except Exception:
                logger.exception("document snapshot failed for %s", document_id)
                yield sse("error", {"detail": "could not load document"})
                return

            client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = client.pubsub()
            await pubsub.subscribe(channel(document_id))

            # Replay anything the client missed, using Last-Event-ID.
            last_id = request.headers.get("Last-Event-ID")
            if last_id and last_id.isdigit():
                from documents.models import DocumentEvent

                async for event in DocumentEvent.objects.filter(
                    document_id=document_id, seq__gt=int(last_id)
                ).order_by("seq")[:200]:
                    yield sse(event.kind, event.payload, id=event.seq)

            while True:
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=5),
                        timeout=HEARTBEAT_INTERVAL_S,
                    )
                except asyncio.TimeoutError:
                    # Comment frames keep proxies from closing an idle stream.
                    yield HEARTBEAT
                    continue

                if message is None:
                    yield HEARTBEAT
                    continue

                try:
                    payload = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                yield sse(
                    payload.get("kind", "message"),
                    payload.get("payload", {}),
                    id=payload.get("seq"),
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("document event stream failed for %s", document_id)
            yield sse("error", {"detail": "stream failed"})
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.aclose()
                except Exception:
                    pass
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

    response = StreamingHttpResponse(generator(), content_type="text/event-stream")
    return stream_headers(response)
