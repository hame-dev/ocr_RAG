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

from common.ownership import (
    CHAT_READY_STATUSES,
    authenticated_user,
    owned_documents,
    unauthorized,
)
from common.sse import sse, stream_headers

logger = logging.getLogger(__name__)

# Graph nodes whose model output is the user-visible answer.
ANSWER_NODES = {"agent", "force_answer"}

# Shown to the user instead of the raw exception, which can leak internals
# (database, Ollama) and means nothing to them. The real error is logged.
GENERATION_FAILED = "The assistant could not finish this answer. Please try again."


# Not csrf_exempt: this POST runs an LLM turn on the user's behalf, so the
# CsrfViewMiddleware check (X-CSRFToken header) applies like any other write.
async def chat_stream(request, conversation_id):
    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=405)

    user = await authenticated_user(request)
    if user is None:
        return unauthorized()

    from chat.graph import SCOPE_ALL, SCOPE_SELECTED, get_graph, resolve_citations
    from chat.models import Conversation, Message
    from chat.research import (
        DEFAULT_CHAT_MODE,
        DEFAULT_RESEARCH_MODE,
        normalize_chat_mode,
        normalize_research_mode,
    )

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid JSON body"}, status=400)

    content = (body.get("content") or "").strip()
    if not content:
        return JsonResponse({"detail": "content is required"}, status=400)

    requested_mode = body.get("research_mode", DEFAULT_RESEARCH_MODE)
    research_mode = normalize_research_mode(requested_mode)
    if research_mode is None:
        return JsonResponse(
            {"detail": "research_mode must be fast, balanced or deep"}, status=400
        )

    chat_mode = normalize_chat_mode(body.get("chat_mode", DEFAULT_CHAT_MODE))
    if chat_mode is None:
        return JsonResponse({"detail": "chat_mode must be documents or general"}, status=400)

    try:
        conversation = await Conversation.objects.aget(id=conversation_id, owner=user)
    except Conversation.DoesNotExist:
        return JsonResponse({"detail": "no such conversation"}, status=404)

    await Message.objects.acreate(
        conversation=conversation,
        role="user",
        content=content,
        usage={"chat_mode": chat_mode},
    )

    # The agent's tools treat doc_ids=None as "the whole corpus", which spans
    # every user. Always hand the graph an explicit list of this user's
    # documents; an empty list means nothing is searchable.
    if chat_mode == "general":
        doc_ids: list[str] = []
        scope_note = ""
    else:
        available = owned_documents(user).filter(status__in=CHAT_READY_STATUSES)
        selected = conversation.scoped_document_ids()
        if selected is not None:
            available = available.filter(id__in=selected)

        doc_ids, titles = [], []
        async for document in available.order_by("created_at"):
            doc_ids.append(str(document.id))
            titles.append(document.display_title)

        if selected is not None:
            scope_note = SCOPE_SELECTED.format(
                count=len(doc_ids), titles=", ".join(titles) or "(none available)"
            )
        else:
            scope_note = SCOPE_ALL

    if not conversation.title:
        conversation.title = content[:120]
        await conversation.asave(update_fields=["title", "updated_at"])

    async def generator():
        from langchain_core.messages import HumanMessage

        buffer: list[str] = []
        # Index in `buffer` where the current model turn started. A turn that
        # ends by calling tools is scaffolding ("let me search…"), not the
        # answer, so its tokens are discarded when it ends.
        turn_start = 0
        retrieved: dict = {}
        search_starts = 0
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
                    "research_mode": research_mode,
                    "chat_mode": chat_mode,
                    "tool_rounds": 0,
                    "retrieved": {},
                    "summary": "",
                },
                config=config,
                version="v2",
            ):
                kind = event["event"]
                node = (event.get("metadata") or {}).get("langgraph_node")

                if kind == "on_chat_model_start" and node in ANSWER_NODES:
                    turn_start = len(buffer)

                elif kind == "on_chat_model_end" and node in ANSWER_NODES:
                    output = event["data"].get("output")
                    if getattr(output, "tool_calls", None) and len(buffer) > turn_start:
                        del buffer[turn_start:]
                        yield sse("reset", {})

                elif kind == "on_chat_model_stream":
                    # astream_events also surfaces the summarizer call inside
                    # `prepare`; only the answering nodes belong in the reply.
                    if node not in ANSWER_NODES:
                        continue
                    chunk = event["data"]["chunk"]
                    token = getattr(chunk, "content", "") or ""
                    if token:
                        buffer.append(token)
                        yield sse("token", {"t": token})

                elif kind == "on_tool_start":
                    tool_name = event["name"]
                    phase = _tool_phase(tool_name, search_starts)
                    if tool_name == "search_documents":
                        search_starts += 1
                    yield sse(
                        "tool_start",
                        {
                            "name": tool_name,
                            "phase": phase,
                            "args": _safe(event["data"].get("input")),
                        },
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
                usage={"research_mode": research_mode, "chat_mode": chat_mode},
            )
            yield sse(
                "done",
                {
                    "message_id": message.seq,
                    "content": text,
                    "citations": citations,
                    "citation_mode": mode,
                    "latency_ms": message.latency_ms,
                    "research_mode": research_mode,
                    "chat_mode": chat_mode,
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
                    usage={"research_mode": research_mode, "chat_mode": chat_mode},
                )
            raise

        except Exception:
            logger.exception("chat stream failed for conversation %s", conversation_id)
            # Always record the failed turn, even with no text, so the question
            # is never left without a reply in the transcript.
            await Message.objects.acreate(
                conversation=conversation,
                role="assistant",
                content="".join(buffer),
                is_partial=True,
                error=GENERATION_FAILED,
                usage={"research_mode": research_mode, "chat_mode": chat_mode},
            )
            yield sse("error", {"detail": GENERATION_FAILED})

    response = StreamingHttpResponse(generator(), content_type="text/event-stream")
    return stream_headers(response)


def _model_id() -> str:
    from django.conf import settings

    return settings.LLM_MODEL


def _tool_phase(tool_name: str, search_starts: int) -> str:
    if tool_name == "search_documents":
        return "searching" if search_starts == 0 else "comparing"
    if tool_name in {"get_chunk_context", "read_document_page", "get_document_metadata"}:
        return "verifying"
    return "reviewing"


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
