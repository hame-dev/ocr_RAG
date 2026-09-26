from __future__ import annotations

import json

import pytest
from asgiref.sync import async_to_sync
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from chat import graph as chat_graph
from chat.models import Conversation, Message
from chat.research import normalize_chat_mode
from documents.models import Document


def _stream(client, conversation_id, **body):
    response = client.post(
        f"/api/conversations/{conversation_id}/stream/",
        data=json.dumps({"content": "What is the capital of France?", **body}),
        content_type="application/json",
    )
    if not response.streaming:
        return response, response.content.decode()

    # The SSE body is an async generator; drain it on the main thread so its
    # ORM writes land inside the test transaction.
    async def drain():
        return b"".join([chunk async for chunk in response.streaming_content])

    return response, async_to_sync(drain)().decode()


def _events(raw: str) -> dict[str, dict]:
    events = {}
    for frame in raw.split("\n\n"):
        name, data = None, None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events[name] = data
    return events


def _fake_llm(calls: list, reply: str = "Paris."):
    def factory(*, with_tools: bool = True, final: bool = False, tools=None, **_):
        calls.append([t.name for t in tools] if with_tools and tools is not None else with_tools)
        model = GenericFakeChatModel(messages=iter([AIMessage(content=reply)]))
        return model.with_config(tags=[chat_graph.FINAL_ANSWER_TAG]) if final else model

    return factory


def test_chat_mode_normalization():
    assert normalize_chat_mode("documents") == "documents"
    assert normalize_chat_mode("general") == "general"
    assert normalize_chat_mode("rag") is None
    assert normalize_chat_mode(None) is None


@pytest.mark.asyncio
async def test_general_mode_binds_only_the_general_tools(monkeypatch):
    calls: list = []
    seen_prompts: list[str] = []

    class RecordingModel(GenericFakeChatModel):
        async def ainvoke(self, messages, *args, **kwargs):
            seen_prompts.append(messages[0].content)
            return await super().ainvoke(messages, *args, **kwargs)

    def factory(*, with_tools: bool = True, tools=None, **_):
        calls.append([t.name for t in tools] if with_tools and tools is not None else with_tools)
        return RecordingModel(messages=iter([AIMessage(content="Paris.")]))

    monkeypatch.setattr(chat_graph, "_llm", factory)

    result = await chat_graph.agent(
        {"messages": [HumanMessage(content="hi")], "chat_mode": "general"}
    )

    # Only run_python: never the document tools.
    assert calls == [["run_python"]]
    assert seen_prompts == [chat_graph.general_system_prompt({})]
    assert "run_python" in seen_prompts[0]
    # No tool calls, so the graph ends the turn instead of routing to tools.
    assert chat_graph._route({"messages": result["messages"]}) == "finalize"


def test_invalid_chat_mode_is_rejected(auth_client, user):
    conversation = Conversation.objects.create(owner=user)

    response, _ = _stream(auth_client, conversation.id, chat_mode="rag")

    assert response.status_code == 400
    assert not Message.objects.exists()


def test_general_mode_turn_is_streamed_and_recorded(auth_client, user, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(chat_graph, "_llm", _fake_llm(calls, reply="Paris is the capital."))
    monkeypatch.setattr(chat_graph, "_graph", None)
    monkeypatch.setattr("chat.checkpointer.get_checkpointer", lambda: None)
    conversation = Conversation.objects.create(owner=user)

    response, raw = _stream(auth_client, conversation.id, chat_mode="general")

    assert response.status_code == 200
    done = _events(raw)["done"]
    assert done["content"] == "Paris is the capital."
    assert done["citations"] == []
    assert done["chat_mode"] == "general"
    assert calls == [["run_python"]]

    assistant = Message.objects.get(conversation=conversation, role="assistant")
    assert assistant.usage["chat_mode"] == "general"
    assert assistant.citations == []
    history = auth_client.get(f"/api/conversations/{conversation.id}/").json()["messages"]
    assert [m["chat_mode"] for m in history] == ["general", "general"]


class _RecordingGraph:
    def __init__(self):
        self.inputs: list[dict] = []

    async def astream_events(self, graph_input, **kwargs):
        self.inputs.append(graph_input)
        if False:  # pragma: no cover - makes this an async generator
            yield {}


@pytest.mark.parametrize("chat_mode", ["documents", "general"])
def test_agent_only_ever_sees_the_users_own_documents(
    auth_client, user, other_user, monkeypatch, chat_mode
):
    fake = _RecordingGraph()
    monkeypatch.setattr(chat_graph, "get_graph", lambda: fake)

    def document(owner, status):
        return Document.objects.create(
            owner=owner,
            original_filename="x.pdf",
            mime_type="application/pdf",
            storage_path="/tmp/x.pdf",
            status=status,
        )

    mine = document(user, "ready")
    document(user, "ocr_running")  # not searchable yet
    document(other_user, "ready")
    conversation = Conversation.objects.create(owner=user)

    response, _ = _stream(auth_client, conversation.id, chat_mode=chat_mode)

    assert response.status_code == 200
    [graph_input] = fake.inputs
    assert graph_input["chat_mode"] == chat_mode
    # Never None: None would mean "every user's documents".
    expected = [str(mine.id)] if chat_mode == "documents" else []
    assert graph_input["doc_ids"] == expected


class _ScriptedGraph:
    """Replays a fixed astream_events sequence (or raises part-way)."""

    def __init__(self, events, error: Exception | None = None):
        self.events = events
        self.error = error

    async def astream_events(self, graph_input, **kwargs):
        for event in self.events:
            yield event
        if self.error:
            raise self.error


def _model_event(kind, *, content="", tool_calls=None):
    from langchain_core.messages import AIMessageChunk

    event = {
        "event": kind, "name": "ChatOllama", "metadata": {"langgraph_node": "agent"},
        "tags": [chat_graph.FINAL_ANSWER_TAG], "data": {},
    }
    if kind == "on_chat_model_stream":
        event["data"]["chunk"] = AIMessageChunk(content=content)
    elif kind == "on_chat_model_end":
        event["data"]["output"] = AIMessage(content=content, tool_calls=tool_calls or [])
    return event


def test_text_from_a_tool_calling_turn_is_not_part_of_the_answer(auth_client, user, monkeypatch):
    tool_call = {"name": "search_documents", "args": {"query": "rent"}, "id": "c1", "type": "tool_call"}
    fake = _ScriptedGraph(
        [
            _model_event("on_chat_model_start"),
            _model_event("on_chat_model_stream", content="Let me search. "),
            _model_event("on_chat_model_end", content="Let me search. ", tool_calls=[tool_call]),
            _model_event("on_chat_model_start"),
            _model_event("on_chat_model_stream", content="The rent is SAR 12,500."),
            _model_event("on_chat_model_end", content="The rent is SAR 12,500."),
        ]
    )
    monkeypatch.setattr(chat_graph, "get_graph", lambda: fake)
    conversation = Conversation.objects.create(owner=user)

    _, raw = _stream(auth_client, conversation.id)

    assert "event: reset" in raw
    assert _events(raw)["done"]["content"] == "The rent is SAR 12,500."
    assert Message.objects.get(role="assistant").content == "The rent is SAR 12,500."


def test_a_failed_turn_is_recorded_without_leaking_the_exception(auth_client, user, monkeypatch):
    fake = _ScriptedGraph([], error=RuntimeError("terminating connection due to administrator command"))
    monkeypatch.setattr(chat_graph, "get_graph", lambda: fake)
    conversation = Conversation.objects.create(owner=user)

    _, raw = _stream(auth_client, conversation.id)

    error = _events(raw)["error"]["detail"]
    assert "administrator" not in error
    reply = Message.objects.get(role="assistant")
    assert reply.error and reply.is_partial and reply.content == ""
