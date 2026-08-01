"""Streaming chat.

A plain async Django view, not DRF — see documents/sse_views.py for why.

Known behaviour worth not fighting: with native tool-calling, token streaming
during the tool-selection turn is lumpy. Rather than a stuttering token feed, the
UI renders tool phases as chips ("searching your documents…") and streams tokens
only for the final answer turn.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt

from common.sse import sse, stream_headers

logger = logging.getLogger(__name__)


@csrf_exempt
async def chat_stream(request, conversation_id):
    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    from chat.graph import SCOPE_ALL, SCOPE_SELECTED, get_graph, resolve_citations
    from chat.models import Conversation, Message
    from documents.models import Document

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid JSON body"}, status=400)

    content = (body.get("content") or "").strip()
    if not content:
        return JsonResponse({"detail": "content is required"}, status=400)

    try:
        conversation = await Conversation.objects.aget(id=conversation_id)
    except Conversation.DoesNotExist:
        return JsonResponse({"detail": "no such conversation"}, status=404)

    await Message.objects.acreate(conversation=conversation, role="user", content=content)

    doc_ids = conversation.scoped_document_ids()
    if doc_ids:
        titles = []
        async for document in Document.objects.filter(id__in=doc_ids):
            titles.append(document.display_title)
        scope_note = SCOPE_SELECTED.format(
            count=len(doc_ids), titles=", ".join(titles) or "(untitled)", ids=doc_ids
        )
    else:
        scope_note = SCOPE_ALL

    if not conversation.title:
        conversation.title = content[:120]
        await conversation.asave(update_fields=["title", "updated_at"])

    async def generator():
        from langchain_core.messages import HumanMessage

        buffer: list[str] = []
        retrieved: dict = {}
        started = time.monotonic()

        try:
            graph = get_graph()
            config = {
                "configurable": {"thread_id": conversation.thread_id},
                "recursion_limit": 24,
            }
            yield sse("start", {"conversation_id": str(conversation.id)})

            async for event in graph.astream_events(
                {
                    "messages": [HumanMessage(content=content)],
                    "doc_ids": doc_ids,
                    "scope_note": scope_note,
                    "tool_iterations": 0,
                    "retrieved": {},
                    "summary": "",
                },
                config=config,
                version="v2",
            ):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    token = getattr(chunk, "content", "") or ""
                    if token:
                        buffer.append(token)
                        yield sse("token", {"t": token})

                elif kind == "on_tool_start":
                    yield sse(
                        "tool_start",
                        {"name": event["name"], "args": _safe(event["data"].get("input"))},
                    )

                elif kind == "on_tool_end":
                    output = event["data"].get("output")
                    hits = _extract_hits(output)
                    for hit in hits:
                        retrieved[str(hit["chunk_id"])] = hit
                    yield sse(
                        "tool_end",
                        {"name": event["name"], "hits": len(hits), "total": len(retrieved)},
                    )

            text, citations, mode = resolve_citations("".join(buffer), retrieved)
            message = await Message.objects.acreate(
                conversation=conversation,
                role="assistant",
                content=text,
                citations=citations,
                citation_mode=mode,
                latency_ms=int((time.monotonic() - started) * 1000),
                model_id=_model_id(),
            )
            yield sse(
                "done",
                {
                    "message_id": message.seq,
                    "content": text,
                    "citations": citations,
                    "citation_mode": mode,
                    "latency_ms": message.latency_ms,
                },
            )

        except asyncio.CancelledError:
            # The user closed the tab. Keep what was generated rather than
            # throwing away a 30-second answer.
            if buffer:
                await Message.objects.acreate(
                    conversation=conversation,
                    role="assistant",
                    content="".join(buffer),
                    is_partial=True,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            raise

        except Exception as exc:
            logger.exception("chat stream failed for conversation %s", conversation_id)
            if buffer:
                await Message.objects.acreate(
                    conversation=conversation,
                    role="assistant",
                    content="".join(buffer),
                    is_partial=True,
                    error=str(exc),
                )
            yield sse("error", {"detail": str(exc)[:400]})

    response = StreamingHttpResponse(generator(), content_type="text/event-stream")
    return stream_headers(response)


def _model_id() -> str:
    from django.conf import settings

    return settings.LLM_MODEL


def _extract_hits(output) -> list[dict]:
    """Pull chunk hits out of a ToolMessage payload, whatever shape it arrived in."""
    payload = output
    if hasattr(payload, "content"):
        payload = payload.content
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if isinstance(payload, list):
        return [h for h in payload if isinstance(h, dict) and h.get("chunk_id")]
    return []


def _safe(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)[:300]
