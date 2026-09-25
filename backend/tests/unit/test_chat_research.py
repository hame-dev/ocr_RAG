from __future__ import annotations

import json
import uuid

import pytest
from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode

from chat.graph import _route
from chat.models import Conversation
from chat.research import RESEARCH_PROFILES, normalize_research_mode
from chat.tools import TOOLS, search_documents
from documents.models import Document


def _document(*, owner, title: str, status: str = "ready") -> Document:
    return Document.objects.create(
        owner=owner,
        title=title,
        original_filename=f"{title}.pdf",
        mime_type="application/pdf",
        storage_path=f"/tmp/{title}.pdf",
        status=status,
    )


@pytest.mark.django_db
def test_conversation_scope_patch_returns_selected_document_summaries(auth_client, user):
    document = _document(owner=user, title="Annual report")
    conversation = Conversation.objects.create(owner=user)

    response = auth_client.patch(
        f"/api/conversations/{conversation.id}/",
        data=json.dumps({"scope": "selected", "document_ids": [str(document.id)]}),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "selected"
    assert payload["document_ids"] == [str(document.id)]
    assert payload["selected_documents"] == [
        {
            "id": str(document.id),
            "display_title": "Annual report",
            "original_filename": "Annual report.pdf",
        }
    ]


@pytest.mark.django_db
def test_conversation_scope_rejects_unavailable_documents(auth_client, user):
    document = _document(owner=user, title="Still processing", status="ocr_running")
    conversation = Conversation.objects.create(owner=user)

    response = auth_client.patch(
        f"/api/conversations/{conversation.id}/",
        data=json.dumps({"scope": "selected", "document_ids": [str(document.id)]}),
        content_type="application/json",
    )

    assert response.status_code == 400
    conversation.refresh_from_db()
    assert conversation.scope == "all"
    assert conversation.document_ids == []


@pytest.mark.django_db
def test_clearing_scope_also_clears_document_ids(auth_client, user):
    document = _document(owner=user, title="Scoped")
    conversation = Conversation.objects.create(
        owner=user, scope="selected", document_ids=[document.id]
    )

    response = auth_client.patch(
        f"/api/conversations/{conversation.id}/",
        data=json.dumps({"scope": "all", "document_ids": [str(document.id)]}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["document_ids"] == []


@pytest.mark.asyncio
async def test_search_tool_receives_authoritative_scope_and_mode(monkeypatch):
    captured = {}

    def fake_search(query, **kwargs):
        captured.update(query=query, **kwargs)
        return []

    monkeypatch.setattr("chat.tools.hybrid_search", fake_search)
    allowed_id = str(uuid.uuid4())
    node = ToolNode([search_documents])
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_documents",
                        "args": {"query": "lease total"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "doc_ids": [allowed_id],
        "research_mode": "deep",
    }

    await node.ainvoke(state)

    assert captured["doc_ids"] == [allowed_id]
    assert captured["top_k"] == RESEARCH_PROFILES["deep"]["top_k"]


def test_scope_arguments_are_hidden_from_every_tool_schema():
    for tool in TOOLS:
        properties = tool.tool_call_schema.model_json_schema()["properties"]
        assert "allowed_doc_ids" not in properties
        assert "research_mode" not in properties


@pytest.mark.parametrize(
    ("mode", "iterations", "expected"),
    [("fast", 2, "force_answer"), ("balanced", 5, "force_answer"), ("deep", 8, "force_answer")],
)
def test_research_profiles_force_an_answer_at_their_tool_cap(mode, iterations, expected):
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_documents",
                        "args": {"query": "again"},
                        "id": "call-cap",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "research_mode": mode,
        "tool_rounds": iterations,
    }

    assert _route(state) == expected


def test_research_mode_validation_and_balanced_default(auth_client):
    conversation_id = uuid.uuid4()
    response = auth_client.post(
        f"/api/conversations/{conversation_id}/stream/",
        data=json.dumps({"content": "hello", "research_mode": "extreme"}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert normalize_research_mode("balanced") == "balanced"
    assert normalize_research_mode("extreme") is None


@pytest.mark.asyncio
async def test_search_retries_without_guessed_filters_when_they_match_nothing(monkeypatch):
    calls = []

    def fake_search(query, **kwargs):
        calls.append(kwargs)
        # Only the unfiltered search finds the passage.
        if kwargs.get("doc_type") or kwargs.get("lang"):
            return []
        return [{"chunk_id": "c1"}]

    monkeypatch.setattr("chat.tools.hybrid_search", fake_search)
    monkeypatch.setattr("chat.tools.format_hits", lambda hits, **_: hits)
    allowed = [str(uuid.uuid4())]

    hits = await search_documents.coroutine(
        query="annual rent", allowed_doc_ids=allowed, research_mode="balanced", doc_type="lease",
    )

    assert hits == [{"chunk_id": "c1"}]
    assert calls[0]["doc_type"] == "lease"
    assert calls[1].get("doc_type") is None
    # The ownership scope is never dropped, only the guessed filters.
    assert all(call["doc_ids"] == allowed for call in calls)
