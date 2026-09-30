from __future__ import annotations

import uuid

import pytest

from chat import code_tools
from chat.deep_think import DEEP_THINK_MAX_TOOL_ROUNDS
from chat.models import Conversation, Message
from documents.models import Document
from tests.fakes import Script, as_json, stream, use_script

pytestmark = pytest.mark.django_db


def _names(events):
    return [name for name, _ in events]


def test_think_streams_reasoning_separately_from_the_answer(auth_client, user, monkeypatch):
    script = Script([{"reasoning": "17 times 3 is 51.", "content": "51"}])
    use_script(monkeypatch, script)
    conversation = Conversation.objects.create(owner=user)

    response, events = stream(auth_client, conversation.id, chat_mode="general", thinking="think")

    assert response.status_code == 200
    assert script.calls == [
        {"with_tools": True, "reasoning": True, "json": False, "final": True, "max_tokens": None,
         "tools": ["run_python"]}
    ]
    thinking = "".join(data["t"] for name, data in events if name == "thinking")
    assert thinking == "17 times 3 is 51."
    done = dict(events)["done"]
    # The reasoning never leaks into the answer text.
    assert done["content"] == "51"
    assert done["reasoning"] == "17 times 3 is 51."
    assert done["thinking_ms"] is not None
    saved = Message.objects.get(role="assistant")
    assert saved.reasoning == "17 times 3 is 51."
    assert saved.usage["thinking"] == "think"


def test_instant_does_not_enable_reasoning(auth_client, user, monkeypatch):
    script = Script(["hello"])
    use_script(monkeypatch, script)
    conversation = Conversation.objects.create(owner=user)

    _, events = stream(auth_client, conversation.id, chat_mode="general", thinking="instant")

    assert script.calls[0]["reasoning"] is False
    assert "thinking" not in _names(events)


def test_invalid_thinking_value_is_rejected(auth_client, user):
    conversation = Conversation.objects.create(owner=user)

    response, _ = stream(auth_client, conversation.id, chat_mode="general", thinking="ultra")

    assert response.status_code == 400
    assert not Message.objects.exists()


def test_deep_think_plans_works_reviews_and_writes(auth_client, user, monkeypatch):
    script = Script([
        as_json({"goal": "Compare two options", "steps": [{"question": "Pros of A"}, {"question": "Pros of B"}]}),
        "A is cheap.",
        "B is fast.",
        as_json({"ok": True, "issues": []}),
        {"reasoning": "Composing.", "content": "A is cheaper; B is faster."},
    ])
    use_script(monkeypatch, script)
    conversation = Conversation.objects.create(owner=user)

    response, events = stream(auth_client, conversation.id, chat_mode="general", thinking="deep")

    assert response.status_code == 200
    phases = [data["phase"] for name, data in events if name == "phase"]
    assert phases == ["planning", "planned", "working", "working", "reviewing", "writing"]
    working = [data for name, data in events if name == "phase" and data["phase"] == "working"]
    assert [(w["index"], w["total"], w["detail"]) for w in working] == [(1, 2, "Pros of A"), (2, 2, "Pros of B")]
    # Thinking is spent only in the final write; the steps are bounded notes.
    assert [c["reasoning"] for c in script.calls] == [False, False, False, False, True]
    assert all(c["max_tokens"] for c in script.calls[1:3])
    done = dict(events)["done"]
    # Planning JSON and notes are never streamed as the answer...
    assert done["content"] == "A is cheaper; B is faster."
    # ...but the notes are the visible reasoning trail, before the writer's thinking.
    assert "A is cheap." in done["reasoning"] and "B is fast." in done["reasoning"]
    assert done["reasoning"].index("B is fast.") < done["reasoning"].index("Composing.")
    assert done["phases"]["steps"] == ["Pros of A", "Pros of B"]
    writer_prompt = script.prompts[-1][0].content
    assert "A is cheap." in writer_prompt and "B is fast." in writer_prompt


def test_deep_think_hands_review_findings_to_the_writer(auth_client, user, monkeypatch):
    script = Script([
        as_json({"goal": "g", "steps": [{"question": "one"}, {"question": "two"}]}),
        "notes one",
        "notes two (wrong)",
        as_json({"ok": False, "issues": [{"step": 2, "problem": "arithmetic error"}]}),
        "final",
    ])
    use_script(monkeypatch, script)
    conversation = Conversation.objects.create(owner=user)

    _, events = stream(auth_client, conversation.id, chat_mode="general", thinking="deep")

    working = [data["index"] for name, data in events if name == "phase" and data["phase"] == "working"]
    assert working == [1, 2]  # no re-work loop
    assert "Step 2: arithmetic error" in script.prompts[-1][0].content
    assert dict(events)["done"]["content"] == "final"


def _plan_one_step() -> list:
    return [
        as_json({"goal": "g", "steps": [{"question": "compute it"}]}),
        "use run_python for the sum",
        as_json({"ok": True, "issues": []}),
    ]


def _run_call(call_id: str, code: str = "print(12 + 5)") -> dict:
    return {"content": "", "tool_calls": [{"name": "run_python", "args": {"code": code}, "id": call_id}]}


def test_deep_think_writer_can_run_python(auth_client, user, monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    seen: list[str] = []

    async def runner(code: str) -> dict:
        seen.append(code)
        return {"ok": True, "stdout": "17\n", "stderr": "", "duration_ms": 5, "files": []}

    monkeypatch.setattr(code_tools, "call_runner", runner)
    script = Script([*_plan_one_step(), _run_call("c1"), "The sum is 17."])
    use_script(monkeypatch, script)
    conversation = Conversation.objects.create(owner=user)

    _, events = stream(auth_client, conversation.id, chat_mode="general", thinking="deep")

    assert seen == ["print(12 + 5)"]
    # The writer is bound to run_python and sees the tool's output on its next call.
    assert script.calls[-1]["tools"] == ["run_python"]
    assert any(m.type == "tool" and "17" in m.content for m in script.prompts[-1])
    runs = [data for name, data in events if name == "code_run"]
    assert len(runs) == 1 and runs[0]["ok"] and runs[0]["stdout"] == "17\n"
    done = dict(events)["done"]
    assert done["content"] == "The sum is 17."
    assert [r["code"] for r in done["tool_calls"]] == ["print(12 + 5)"]


def test_deep_think_writer_is_forced_to_answer_after_the_tool_cap(auth_client, user, monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)

    async def runner(code: str) -> dict:
        return {"ok": False, "stdout": "", "stderr": "NameError", "duration_ms": 5, "files": []}

    monkeypatch.setattr(code_tools, "call_runner", runner)
    greedy = [_run_call(f"c{i}") for i in range(DEEP_THINK_MAX_TOOL_ROUNDS + 1)]
    script = Script([*_plan_one_step(), *greedy, "Could not finish the calculation."])
    use_script(monkeypatch, script)
    conversation = Conversation.objects.create(owner=user)

    _, events = stream(auth_client, conversation.id, chat_mode="general", thinking="deep")

    assert len([1 for name, _ in events if name == "code_run"]) == DEEP_THINK_MAX_TOOL_ROUNDS
    assert script.calls[-1]["with_tools"] is False
    assert "code-run budget for this turn is exhausted" in script.prompts[-1][0].content
    assert dict(events)["done"]["content"] == "Could not finish the calculation."


def test_system_prompts_carry_the_bayan_identity(auth_client, user, monkeypatch):
    script = Script(["hello"])
    use_script(monkeypatch, script)
    conversation = Conversation.objects.create(owner=user)

    stream(auth_client, conversation.id, chat_mode="general", thinking="instant")

    assert "You are Bayan" in script.prompts[0][0].content


def _hit(document, chunk_id, text):
    return {
        "chunk_id": chunk_id, "document_id": str(document.id), "document_title": document.title,
        "original_filename": "lease.pdf", "page_start": 1, "page_end": 1, "section_path": "",
        "text": text, "score": 0.9,
    }


def test_deep_research_retries_gaps_cites_and_suggests_follow_ups(auth_client, user, other_user, monkeypatch):
    mine = Document.objects.create(owner=user, title="Lease", original_filename="lease.pdf",
                                   mime_type="application/pdf", storage_path="/tmp/l.pdf", status="ready")
    Document.objects.create(owner=other_user, title="Not mine", original_filename="x.pdf",
                            mime_type="application/pdf", storage_path="/tmp/x.pdf", status="ready")
    rent_id, parties_id = str(uuid.uuid4()), str(uuid.uuid4())
    searches = []

    def fake_search(query, *, top_k, doc_ids, **_):
        searches.append((query, doc_ids))
        if "rent" in query:
            return [_hit(mine, rent_id, "Annual rent SAR 12,500")]
        if "landlord" in query:
            return [_hit(mine, parties_id, "Between Hame Trading and Ahmed")]
        return []

    monkeypatch.setattr("chat.deep_research.hybrid_search", fake_search)
    script = Script([
        as_json({"understanding": "rent and parties", "steps": [
            {"question": "What is the rent?", "query": "annual rent"},
            {"question": "Who are the parties?", "query": "parties"},
        ]}),
        as_json({"results": [
            {"question": "What is the rent?", "confidence": 0.9, "answer": "12,500"},
            {"question": "Who are the parties?", "confidence": 0.1, "answer": ""},
        ]}),
        as_json({"queries": ["landlord tenant names"]}),
        as_json({"results": [{"question": "Who are the parties?", "confidence": 0.85, "answer": "Hame, Ahmed"}]}),
        {"content": f"Rent is SAR 12,500 [[cite:{rent_id}]]. Parties: Hame and Ahmed [[cite:{parties_id}]]."},
        as_json({"questions": ["When does it start?", "Is it renewable?", "Who signed?"]}),
    ])
    use_script(monkeypatch, script)
    conversation = Conversation.objects.create(owner=user)

    response, events = stream(auth_client, conversation.id, chat_mode="documents", research_mode="deep")

    assert response.status_code == 200
    # Gaps were retried with the alternative query, and only the user's own
    # documents were ever searched.
    assert [q for q, _ in searches] == ["annual rent", "parties", "landlord tenant names"]
    assert all(ids == [str(mine.id)] for _, ids in searches)
    done = dict(events)["done"]
    assert [c["chunk_id"] for c in done["citations"]] == [rent_id, parties_id]
    assert done["citation_mode"] == "explicit"
    assert "[1]" in done["content"] and "[[cite:" not in done["content"]
    assert done["follow_ups"] == ["When does it start?", "Is it renewable?", "Who signed?"]
    assert done["phases"]["queries"] == ["annual rent", "parties", "landlord tenant names"]
    saved = Message.objects.get(role="assistant")
    assert saved.usage["follow_ups"] == done["follow_ups"]
