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
import uuid

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

    from chat import attachments as chat_attachments
    from chat.models import Conversation
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
    except ValueError:  # JSONDecodeError, and UnicodeDecodeError on non-UTF-8 bodies
        return JsonResponse({"detail": "invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"detail": "the body must be a JSON object"}, status=400)

    content = body.get("content") or ""
    if not isinstance(content, str):
        return JsonResponse({"detail": "content must be a string"}, status=400)
    content = content.strip()
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
        attachment_ids = [str(uuid.UUID(str(a))) for a in attachment_ids]
    except ValueError:
        return JsonResponse({"detail": "attachment_ids must be UUIDs"}, status=400)

    try:
        conversation = await Conversation.objects.aget(id=conversation_id, owner=user)
    except Conversation.DoesNotExist:
        return JsonResponse({"detail": "no such conversation"}, status=404)

    # One turn at a time per conversation: two concurrent runs on the same
    # LangGraph thread each start from the same checkpoint, and one turn is
    # silently dropped from the agent's memory.
    lock = await _acquire_turn_lock(conversation.id)
    if lock is None:
        return JsonResponse(
            {"detail": "this conversation is already answering; wait for it to finish"},
            status=409,
        )
    try:
        response = await _start_turn(
            user, conversation, content, research_mode, chat_mode, thinking,
            attachment_ids, lock,
        )
    except BaseException:
        await _release_turn_lock(lock)
        raise
    if not isinstance(response, StreamingHttpResponse):
        # Rejected before streaming; the stream's own `finally` releases otherwise.
        await _release_turn_lock(lock)
    return response


async def _start_turn(user, conversation, content, research_mode, chat_mode,
                      thinking, attachment_ids, lock):
    """Everything after validation. Owns `lock` once the stream starts."""
    from asgiref.sync import sync_to_async

    from chat import attachments as chat_attachments
    from chat.code_tools import serialize_file
    from chat.graph import (
        FINAL_ANSWER_TAG, SCOPE_ALL, SCOPE_SELECTED, get_graph, resolve_citations,
    )
    from chat.models import ChatAttachment, GeneratedFile, Message

    conversation_id = conversation.id

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
        # Re-checked in the UPDATE itself, so an attachment raced into another
        # message between the lookup and here is not stolen.
        bound = await ChatAttachment.objects.filter(
            id__in=[a.id for a in attachments], message__isnull=True
        ).aupdate(conversation=conversation, message=user_message)
        if bound != len(attachments):
            await user_message.adelete()
            return JsonResponse({"detail": "unknown or already-used attachment"}, status=404)

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

    # Always bumped, so the sidebar orders conversations by latest activity.
    update_fields = ["updated_at"]
    if not conversation.title:
        conversation.title = content[:120]
        update_fields.append("title")
    await conversation.asave(update_fields=update_fields)

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
        # General mode's run_python calls: what the UI shows, and the files
        # they saved, bound to the assistant message once it exists.
        code_runs: list[dict] = []
        file_ids: list[str] = []
        # run_python calls started but not ended, by run id -> their code. A
        # call whose arguments fail validation raises before the tool body runs
        # and never reaches on_tool_end; these are reported as failed runs.
        pending_runs: dict[str, str] = {}
        started = time.monotonic()

        def unfinished_runs() -> list[dict]:
            runs = [
                _code_run(code, {"stderr": "The code could not be run: the tool call was invalid."})
                for code in pending_runs.values()
            ]
            pending_runs.clear()
            code_runs.extend(runs)
            return runs

        saved = False

        async def save_reply(**fields) -> Message:
            nonlocal saved
            saved = True
            message = await Message.objects.acreate(
                conversation=conversation, role="assistant", tool_calls=code_runs, **fields
            )
            if file_ids:
                await GeneratedFile.objects.filter(id__in=file_ids).aupdate(message=message)
            return message

        async def load_files(ids: list[str]) -> list[dict]:
            files = {
                str(f.id): f
                async for f in GeneratedFile.objects.filter(id__in=ids, owner=user)
            }
            return [serialize_file(files[i]) for i in ids if i in files]

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
                    "user_id": user.pk,
                    "conversation_id": str(conversation.id),
                    "scope_note": scope_note,
                    "research_mode": research_mode,
                    "chat_mode": chat_mode,
                    "thinking": thinking,
                    "tool_rounds": 0,
                    "retrieved": {},
                    "follow_ups": [],
                    # Not "summary": it is carried over from the checkpoint,
                    # and resetting it here would erase the summarized turns.
                },
                config=config,
                version="v2",
            ):
                kind = event["event"]

                if kind == "on_chat_model_start":
                    # The model is answering again, so every tool call has settled.
                    for run in unfinished_runs():
                        yield sse("code_run", {**run, "files": []})
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
                    args = event["data"].get("input")
                    if tool_name == "run_python":
                        # Only the code: the injected owner/conversation ids are not UI data.
                        code = str(args.get("code") or "") if isinstance(args, dict) else ""
                        pending_runs[str(event.get("run_id"))] = code
                        args = {"code": code}
                    elif isinstance(args, dict):
                        # Injected graph state (the user's document ids, mode)
                        # is not the model's arguments and not UI data.
                        args = {k: v for k, v in args.items() if k not in _INJECTED_ARGS}
                    yield sse(
                        "tool_start",
                        {"name": tool_name, "phase": phase, "args": _safe(args)},
                    )

                elif kind == "on_tool_end" and event["name"] == "run_python":
                    code = pending_runs.pop(str(event.get("run_id")), "")
                    artifact = getattr(event["data"].get("output"), "artifact", None) or {}
                    run = _code_run(artifact.get("code") or code, artifact)
                    file_ids.extend(run["file_ids"])
                    code_runs.append(run)
                    yield sse("code_run", {**run, "files": await load_files(run["file_ids"])})

                elif kind == "on_tool_end":
                    output = event["data"].get("output")
                    hits = _extract_hits(output)
                    for hit in hits:
                        retrieved[str(hit["chunk_id"])] = hit
                    yield sse(
                        "tool_end",
                        {"name": event["name"], "hits": len(hits), "total": len(retrieved)},
                    )

            for run in unfinished_runs():
                yield sse("code_run", {**run, "files": []})
            if thinking_started is not None and thinking_ms is None:
                thinking_ms = int((time.monotonic() - thinking_started) * 1000)
            text, citations, mode = resolve_citations("".join(buffer), retrieved)
            summary = _phase_summary(phases)
            message = await save_reply(
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
                    "tool_calls": code_runs,
                    "files": await load_files(file_ids),
                },
            )

        except (asyncio.CancelledError, GeneratorExit):
            # The user closed the tab. Keep what was generated rather than
            # throwing away a 30-second answer. GeneratorExit too: a
            # disconnect while suspended at a `yield` closes the generator
            # instead of cancelling it.
            if not saved and (buffer or reasoning or code_runs):
                await _save_quietly(
                    save_reply,
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
            # is never left without a reply in the transcript. If that save
            # fails too (the conversation was deleted mid-stream), the client
            # must still get the error frame.
            if not saved:
                await _save_quietly(
                    save_reply,
                    content="".join(buffer),
                    is_partial=True,
                    error=GENERATION_FAILED,
                    reasoning="".join(reasoning),
                    usage={"research_mode": research_mode, "chat_mode": chat_mode, "thinking": thinking},
                )
            yield sse("error", {"detail": GENERATION_FAILED})

        finally:
            from chat.checkpointer import prune_thread

            try:
                await prune_thread(conversation.thread_id)
            finally:
                await _release_turn_lock(lock)

    response = StreamingHttpResponse(generator(), content_type="text/event-stream")
    return stream_headers(response)


# Tool arguments filled from graph state (InjectedState), not by the model.
_INJECTED_ARGS = {"allowed_doc_ids", "research_mode", "user_id", "conversation_id"}


# Longer than any turn can take (deep research included), so a crashed worker
# cannot lock a conversation for more than this.
TURN_LOCK_TTL_S = 15 * 60


def _turn_lock_key(conversation_id) -> str:
    return f"chat:turn:{conversation_id}"


async def _acquire_turn_lock(conversation_id):
    """A (key, token) pair if this request may run a turn, else None.

    Redis unavailable means no lock rather than no chat.
    """
    import redis.asyncio as aioredis
    from django.conf import settings

    key, token = _turn_lock_key(conversation_id), uuid.uuid4().hex
    try:
        client = aioredis.from_url(settings.REDIS_URL)
        try:
            acquired = await client.set(key, token, nx=True, ex=TURN_LOCK_TTL_S)
        finally:
            await client.aclose()
    except Exception:
        logger.warning("chat turn lock unavailable; continuing without it", exc_info=True)
        return (key, None)
    return (key, token) if acquired else None


# Deletes the key only if it still holds our token, so an expired lock that
# another turn has since taken is left alone.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end
return 0
"""


async def _release_turn_lock(lock) -> None:
    key, token = lock
    if token is None:
        return
    import redis.asyncio as aioredis
    from django.conf import settings

    try:
        client = aioredis.from_url(settings.REDIS_URL)
        try:
            await client.eval(_RELEASE_SCRIPT, 1, key, token)
        finally:
            await client.aclose()
    except Exception:
        logger.warning("could not release chat turn lock %s", key, exc_info=True)


async def _save_quietly(save_reply, **fields) -> None:
    try:
        await save_reply(**fields)
    except Exception:
        logger.exception("could not save the assistant reply")


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
    if tool_name == "run_python":
        return "running_code"
    if tool_name in {"get_chunk_context", "read_document_page", "get_document_metadata"}:
        return "verifying"
    return "reviewing"


def _code_run(code: str, artifact: dict) -> dict:
    """One run_python call as the UI shows it and Message.tool_calls stores it."""
    return {
        "name": "run_python",
        "code": code,
        "ok": bool(artifact.get("ok")),
        "stdout": artifact.get("stdout") or "",
        "stderr": artifact.get("stderr") or "",
        "duration_ms": artifact.get("duration_ms"),
        "file_ids": [str(i) for i in artifact.get("file_ids") or []],
    }


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
