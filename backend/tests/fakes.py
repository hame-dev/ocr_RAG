"""Test doubles for the chat model and helpers to drive the SSE endpoint."""
from __future__ import annotations

import json
from typing import Any, Callable

from asgiref.sync import async_to_sync
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class ScriptedChatModel(BaseChatModel):
    """Replies from a shared script, streaming reasoning like qwen does.

    Each step is {"content": str, "reasoning": str}. Every model instance the
    graph creates pulls from the same script, in call order.
    """

    next_step: Callable[[list], dict]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        step = self.next_step(messages)
        extra = {"reasoning_content": step["reasoning"]} if step.get("reasoning") else {}
        return ChatResult(generations=[ChatGeneration(
            message=AIMessage(content=step.get("content", ""), additional_kwargs=extra)
        )])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        step = self.next_step(messages)
        for piece in _pieces(step.get("reasoning", "")):
            chunk = ChatGenerationChunk(message=AIMessageChunk(
                content="", additional_kwargs={"reasoning_content": piece}
            ))
            if run_manager:
                await run_manager.on_llm_new_token("", chunk=chunk)
            yield chunk
        for piece in _pieces(step.get("content", "")):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
            if run_manager:
                await run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk


def _pieces(text: str) -> list[str]:
    words = text.split(" ")
    return [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words) if w or i == 0] if text else []


class Script:
    """A factory to monkeypatch in place of chat.graph._llm."""

    def __init__(self, steps: list[dict | str]):
        self.steps = [s if isinstance(s, dict) else {"content": s} for s in steps]
        self.calls: list[dict] = []   # kwargs of each _llm() call
        self.prompts: list[list] = []  # messages of each model invocation

    def _next(self, messages: list) -> dict:
        self.prompts.append(messages)
        return self.steps.pop(0) if self.steps else {"content": ""}

    def __call__(self, *, with_tools: bool = True, reasoning: bool = False,
                 json: bool = False, final: bool = False, max_tokens: int | None = None):
        from chat.graph import FINAL_ANSWER_TAG

        self.calls.append({"with_tools": with_tools, "reasoning": reasoning, "json": json, "final": final,
                           "max_tokens": max_tokens})
        model = ScriptedChatModel(next_step=self._next)
        return model.with_config(tags=[FINAL_ANSWER_TAG]) if final else model


def as_json(value: Any) -> dict:
    return {"content": json.dumps(value)}


def stream(client, conversation_id, **body) -> tuple[Any, list[tuple[str, dict]]]:
    """POST a chat turn and return (response, [(event, data), ...])."""
    payload = {"content": "question", **body}
    response = client.post(
        f"/api/conversations/{conversation_id}/stream/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    if not response.streaming:
        return response, []

    async def drain():
        return b"".join([chunk async for chunk in response.streaming_content])

    raw = async_to_sync(drain)().decode()
    events = []
    for frame in raw.split("\n\n"):
        name, data = None, None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return response, events


def use_script(monkeypatch, script: Script) -> None:
    from chat import graph as chat_graph

    monkeypatch.setattr(chat_graph, "_llm", script)
    monkeypatch.setattr(chat_graph, "_graph", None)
    monkeypatch.setattr("chat.checkpointer.get_checkpointer", lambda: None)
