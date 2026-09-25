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

    from asgiref.sync import sync_to_async

    from chat import attachments as chat_attachments
    from chat.graph import (
        FINAL_ANSWER_TAG, SCOPE_ALL, SCOPE_SELECTED, get_graph, resolve_citations,
    )
    from chat.models import ChatAttachment, Conversation, Message
    from chat.research import (
        DEFAULT_CHAT_MODE,
        DEFAULT_RESEARCH_MODE,
        DEFAULT_THINKING,
        normalize_chat_mode,
        normalize_research_mode,
        normalize_thinking,
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

    thinking = normalize_thinking(body.get("thinking", DEFAULT_THINKING))
    if thinking is None:
        return JsonResponse({"detail": "thinking must be instant, think or deep"}, status=400)
    if chat_mode != "general":
        # Documents mode has its own depth control (research_mode).
        thinking = DEFAULT_THINKING

    attachment_ids = body.get("attachment_ids") or []
    if not isinstance(attachment_ids, list) or len(attachment_ids) > chat_attachments.MAX_ATTACHMENTS_PER_MESSAGE:
        return JsonResponse(
            {"detail": f"attachment_ids must be a list of at most "
                       f"{chat_attachments.MAX_ATTACHMENTS_PER_MESSAGE}"},
            status=400,
        )

    try:
        conversation = await Conversation.objects.aget(id=conversation_id, owner=user)
    except Conversation.DoesNotExist:
        return JsonResponse({"detail": "no such conversation"}, status=404)

    # Only the caller's own uploads, and only ones not already sent elsewhere.
    attachments: list[ChatAttachment] = []
    if attachment_ids:
        wanted = [str(a) for a in attachment_ids]
        found = {
            str(a.id): a
            async for a in ChatAttachment.objects.filter(id__in=wanted, owner=user, message__isnull=True)
        }
        if len(found) != len(set(wanted)):
            return JsonResponse({"detail": "unknown or already-used attachment"}, status=404)
        attachments = [found[a] for a in dict.fromkeys(wanted)]

    user_message = await Message.objects.acreate(
        conversation=conversation,
        role="user",
        content=content,
        usage={"chat_mode": chat_mode, "thinking": thinking},
    )
    if attachments:
        await ChatAttachment.objects.filter(id__in=[a.id for a in attachments]).aupdate(
            conversation=conversation, message=user_message
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
        reasoning: list[str] = []
        new_reasoning_run = False
        thinking_started: float | None = None
        thinking_ms: int | None = None
        phases: list[dict] = []
        follow_ups: list[str] = []
        retrieved: dict = {}
        search_starts = 0
        started = time.monotonic()

        def is_answer(event) -> bool:
            # Only calls tagged as producing the answer stream as answer text;
            # the summarizer and the pipelines' planning/checking calls do not.
            return FINAL_ANSWER_TAG in (event.get("tags") or [])

        try:
            graph = get_graph()
            config = {
                "configurable": {"thread_id": conversation.thread_id},
                # Deep research runs several nodes' worth of steps per turn.
                "recursion_limit": 40,
            }
            yield sse("start", {"conversation_id": str(conversation.id)})

            history_images = 0
            if attachments:
                try:
                    snapshot = await graph.aget_state(config)
                    history_images = chat_attachments.count_history_images(
                        (snapshot.values or {}).get("messages", [])
                    )
                except Exception:
                    history_images = 0  # no checkpointer (tests) or empty thread
            human_content = await sync_to_async(chat_attachments.build_human_content)(
                content, attachments, history_images
            )

            async for event in graph.astream_events(
                {
                    "messages": [HumanMessage(content=human_content)],
                    "doc_ids": doc_ids,
                    "scope_note": scope_note,
                    "research_mode": research_mode,
                    "chat_mode": chat_mode,
                    "thinking": thinking,
                    "tool_rounds": 0,
                    "retrieved": {},
                    "follow_ups": [],
                    "summary": "",
                },
                config=config,
                version="v2",
            ):
                kind = event["event"]

                if kind == "on_chat_model_start":
                    new_reasoning_run = True
                    if is_answer(event):
                        turn_start = len(buffer)

                elif kind == "on_chat_model_end" and is_answer(event):
                    output = event["data"].get("output")
                    if getattr(output, "tool_calls", None) and len(buffer) > turn_start:
                        del buffer[turn_start:]
                        yield sse("reset", {})

                elif kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    thought = (getattr(chunk, "additional_kwargs", None) or {}).get("reasoning_content") or ""
                    if thought:
                        if thinking_started is None:
                            thinking_started = time.monotonic()
                        # Separate the reasoning of consecutive model calls
                        # (deep think runs one per step).
                        if new_reasoning_run and reasoning:
                            thought = "\n\n" + thought
                        new_reasoning_run = False
                        reasoning.append(thought)
                        yield sse("thinking", {"t": thought})
                    if not is_answer(event):
                        continue
                    token = getattr(chunk, "content", "") or ""
                    if token:
                        if thinking_started is not None and thinking_ms is None:
                            thinking_ms = int((time.monotonic() - thinking_started) * 1000)
                        buffer.append(token)
                        yield sse("token", {"t": token})

                elif kind == "on_custom_event":
                    data = event.get("data") or {}
                    if event["name"] == "phase":
                        phases.append(data)
                        yield sse("phase", data)
                    elif event["name"] == "note":
                        # Deep think's working notes are its visible reasoning trail.
                        if thinking_started is None:
                            thinking_started = time.monotonic()
                        note = f"**{data.get('index')}. {data.get('question')}**\n{data.get('text')}"
                        if reasoning:
                            note = "\n\n" + note
                        new_reasoning_run = True
                        reasoning.append(note)
                        yield sse("thinking", {"t": note})
                    elif event["name"] == "sources":
                        for hit in data.get("hits") or []:
                            if isinstance(hit, dict) and hit.get("chunk_id"):
                                retrieved[str(hit["chunk_id"])] = hit
                    elif event["name"] == "follow_ups":
                        follow_ups = [q for q in data.get("questions") or [] if isinstance(q, str)][:3]

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

            if thinking_started is not None and thinking_ms is None:
                thinking_ms = int((time.monotonic() - thinking_started) * 1000)
            text, citations, mode = resolve_citations("".join(buffer), retrieved)
            summary = _phase_summary(phases)
            message = await Message.objects.acreate(
                conversation=conversation,
                role="assistant",
                content=text,
                citations=citations,
                citation_mode=mode,
                latency_ms=int((time.monotonic() - started) * 1000),
                model_id=_model_id(),
                reasoning="".join(reasoning),
                thinking_ms=thinking_ms,
                usage={
                    "research_mode": research_mode,
                    "chat_mode": chat_mode,
                    "thinking": thinking,
                    "follow_ups": follow_ups,
                    "phases": summary,
                },
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
                    "thinking": thinking,
                    "reasoning": message.reasoning,
                    "thinking_ms": thinking_ms,
                    "follow_ups": follow_ups,
                    "phases": summary,
                },
            )

        except asyncio.CancelledError:
            # The user closed the tab. Keep what was generated rather than
            # throwing away a 30-second answer.
            if buffer or reasoning:
                await Message.objects.acreate(
                    conversation=conversation,
                    role="assistant",
                    content="".join(buffer),
                    is_partial=True,
                    reasoning="".join(reasoning),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    usage={"research_mode": research_mode, "chat_mode": chat_mode, "thinking": thinking},
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
                reasoning="".join(reasoning),
                usage={"research_mode": research_mode, "chat_mode": chat_mode, "thinking": thinking},
            )
            yield sse("error", {"detail": GENERATION_FAILED})

    response = StreamingHttpResponse(generator(), content_type="text/event-stream")
    return stream_headers(response)


def _phase_summary(phases: list[dict]) -> dict | None:
    """What a finished pipeline did, compact enough to store with the message."""
    if not phases:
        return None
    planned = next((p for p in phases if p.get("phase") == "planned"), {})
    return {
        "steps": planned.get("steps") or [],
        "queries": [p.get("detail") for p in phases if p.get("phase") == "searching"],
        "sources": next((p.get("sources") for p in phases if p.get("phase") == "writing"), None),
    }


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
