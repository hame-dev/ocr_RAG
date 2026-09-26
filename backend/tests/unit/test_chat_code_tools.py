from __future__ import annotations

import base64
import io
import json

import pytest
from asgiref.sync import async_to_sync
from langchain_core.messages import AIMessage
from PIL import Image

from chat import code_tools
from chat import graph as chat_graph
from chat.models import Conversation, GeneratedFile, Message
from tests.fakes import Script, stream, use_script

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "blue").save(buf, "PNG")
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _runner(result: dict | Exception, seen: list | None = None):
    async def fake(code: str) -> dict:
        if seen is not None:
            seen.append(code)
        if isinstance(result, Exception):
            raise result
        return result

    return fake


OK_RESULT = {
    "ok": True, "exit_code": 0, "timed_out": False, "stdout": "17\n", "stderr": "", "duration_ms": 42,
    "files": [
        {"name": "chart.png", "b64": _b64(_png())},
        {"name": "../../etc/evil.sh", "b64": _b64(b"rm -rf /")},  # disallowed type
        {"name": "scores.xlsx", "b64": _b64(b"PK\x03\x04fake")},
    ],
}


def _invoke(**args):
    call = {"type": "tool_call", "name": "run_python", "id": "c1", "args": args}
    return async_to_sync(code_tools.run_python.ainvoke)(call)


def test_run_python_saves_allowed_files_and_splits_model_and_ui_output(user, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(code_tools, "call_runner", _runner(OK_RESULT, seen))
    conversation = Conversation.objects.create(owner=user)

    message = _invoke(code="print(12 + 5)", user_id=user.pk, conversation_id=str(conversation.id))

    assert seen == ["print(12 + 5)"]
    content = json.loads(message.content)
    assert content == {"ok": True, "stdout": "17\n", "stderr": "", "files_saved": ["chart.png", "scores.xlsx"]}
    files = list(GeneratedFile.objects.order_by("created_at"))
    assert [(f.filename, f.kind, f.owner_id, f.conversation_id) for f in files] == [
        ("chart.png", "image", user.pk, conversation.id),
        ("scores.xlsx", "spreadsheet", user.pk, conversation.id),
    ]
    assert message.artifact["code"] == "print(12 + 5)"
    assert message.artifact["file_ids"] == [str(f.id) for f in files]
    assert all(f.message_id is None for f in files)


def test_run_python_reports_an_unavailable_runner_without_raising(user, monkeypatch):
    monkeypatch.setattr(
        code_tools, "call_runner", _runner(code_tools.RunnerUnavailable("the code runner is not reachable"))
    )
    conversation = Conversation.objects.create(owner=user)

    message = _invoke(code="print(1)", user_id=user.pk, conversation_id=str(conversation.id))

    assert json.loads(message.content)["ok"] is False
    assert "not reachable" in message.artifact["stderr"]
    assert not GeneratedFile.objects.exists()


@pytest.mark.parametrize("name, expected", [
    ("chart.png", "chart.png"),
    ("../../x/report.DOCX", "report.docx"),
    ("my deck (final).pptx", "my deck _final.pptx"),
    ("script.py", None),
    ("noextension", None),
])
def test_safe_filename(name, expected):
    assert code_tools.safe_filename(name) == expected


def test_general_turn_runs_code_and_binds_the_files_to_the_answer(auth_client, user, monkeypatch):
    monkeypatch.setattr(code_tools, "call_runner", _runner(OK_RESULT))
    call = {"name": "run_python", "args": {"code": "print(12 + 5)"}, "id": "call-1", "type": "tool_call"}
    script = Script([
        {"content": "", "tool_calls": [call]},
        {"content": "12 + 5 = 17. The chart and spreadsheet are below."},
    ])
    use_script(monkeypatch, script)
    conversation = Conversation.objects.create(owner=user)

    response, events = stream(auth_client, conversation.id, chat_mode="general")

    assert response.status_code == 200
    names = [name for name, _ in events]
    assert "error" not in names, events
    start = next(data for name, data in events if name == "tool_start")
    assert start == {"name": "run_python", "phase": "running_code", "args": {"code": "print(12 + 5)"}}
    run = next(data for name, data in events if name == "code_run")
    assert run["code"] == "print(12 + 5)" and run["ok"] and run["stdout"] == "17\n"
    assert [f["filename"] for f in run["files"]] == ["chart.png", "scores.xlsx"]

    done = dict(events)["done"]
    assert done["content"] == "12 + 5 = 17. The chart and spreadsheet are below."
    assert [f["filename"] for f in done["files"]] == ["chart.png", "scores.xlsx"]
    assert done["files"][0]["inline"] is True and done["files"][1]["inline"] is False

    assistant = Message.objects.get(role="assistant")
    assert assistant.tool_calls[0]["code"] == "print(12 + 5)"
    assert GeneratedFile.objects.filter(message=assistant).count() == 2

    history = auth_client.get(f"/api/conversations/{conversation.id}/").json()["messages"]
    assert [f["filename"] for f in history[1]["files"]] == ["chart.png", "scores.xlsx"]
    assert history[1]["tool_calls"][0]["stdout"] == "17\n"
    # The model saw the tool's small content, not the file bytes.
    tool_message = script.prompts[1][-1]
    assert tool_message.type == "tool" and "files_saved" in tool_message.content


def test_documents_mode_can_never_run_code():
    graph = chat_graph.build_graph()
    documents_tools = graph.nodes["tools"].bound.tools_by_name
    general_tools = graph.nodes["general_tools"].bound.tools_by_name
    assert "run_python" not in documents_tools
    assert set(general_tools) == {"run_python"}


def test_route_sends_general_tool_calls_to_the_general_node_and_caps_them():
    call = {"name": "run_python", "args": {"code": "1"}, "id": "c", "type": "tool_call"}
    messages = [AIMessage(content="", tool_calls=[call])]

    assert chat_graph._route({"messages": messages, "chat_mode": "general", "tool_rounds": 0}) == "general_tools"
    assert chat_graph._route({
        "messages": messages, "chat_mode": "general", "tool_rounds": chat_graph.GENERAL_MAX_TOOL_ROUNDS,
    }) == "force_answer"
    search = {"name": "search_documents", "args": {"query": "x"}, "id": "d", "type": "tool_call"}
    assert chat_graph._route({
        "messages": [AIMessage(content="", tool_calls=[search])], "chat_mode": "documents", "tool_rounds": 0,
    }) == "tools"


def _stored(user, conversation, name, data, mime, kind="image"):
    record = GeneratedFile(owner=user, conversation=conversation, filename=name, mime=mime, kind=kind, size=len(data))
    directory = code_tools.generated_dir(record.id)
    import os

    os.makedirs(directory, exist_ok=True)
    record.storage_path = os.path.join(directory, name)
    with open(record.storage_path, "wb") as fh:
        fh.write(data)
    record.save()
    return record


def test_download_is_owner_only_and_only_raster_images_render_inline(auth_client, other_client, user):
    conversation = Conversation.objects.create(owner=user)
    png = _stored(user, conversation, "chart.png", _png(), "image/png")
    svg = _stored(user, conversation, "chart.svg", b"<svg onload='alert(1)'/>", "image/svg+xml")

    response = auth_client.get(f"/api/chat/files/{png.id}/")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response["Content-Disposition"].startswith("inline")
    assert response["X-Content-Type-Options"] == "nosniff"

    response = auth_client.get(f"/api/chat/files/{svg.id}/")
    assert response["Content-Disposition"].startswith("attachment")

    assert other_client.get(f"/api/chat/files/{png.id}/").status_code == 404


def test_deleting_a_conversation_removes_its_files_from_disk(user):
    import os

    conversation = Conversation.objects.create(owner=user)
    record = _stored(user, conversation, "chart.png", _png(), "image/png")
    assert os.path.exists(record.storage_path)

    conversation.delete()

    assert not os.path.exists(code_tools.generated_dir(record.id))


def test_an_unexpected_tool_failure_is_a_failed_run_not_an_exception(user, monkeypatch):
    monkeypatch.setattr(code_tools, "call_runner", _runner(ValueError("runner sent invalid JSON")))
    conversation = Conversation.objects.create(owner=user)

    message = _invoke(code="print(1)", user_id=user.pk, conversation_id=str(conversation.id))

    assert json.loads(message.content)["ok"] is False
    assert message.artifact["code"] == "print(1)" and not message.artifact["ok"]


def _code_runs(events):
    return [data for name, data in events if name == "code_run"]


def test_an_invalid_tool_call_is_reported_as_a_failed_run(auth_client, user, monkeypatch):
    # No `code` argument: the tool raises before its body runs, so there is no on_tool_end.
    bad = {"name": "run_python", "args": {}, "id": "call-1", "type": "tool_call"}
    script = Script([{"content": "", "tool_calls": [bad]}, {"content": "Sorry, that failed."}])
    use_script(monkeypatch, script)
    conversation = Conversation.objects.create(owner=user)

    _, events = stream(auth_client, conversation.id, chat_mode="general")

    [run] = _code_runs(events)
    assert run["ok"] is False and "invalid" in run["stderr"]
    # Reported before the answer streams, so the UI's "Running code…" chip clears.
    names = [name for name, _ in events]
    assert names.index("code_run") < names.index("token")
    assert Message.objects.get(role="assistant").tool_calls == [
        {k: v for k, v in run.items() if k != "files"}
    ]


def test_parallel_runs_each_keep_their_own_code(auth_client, user, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(code_tools, "call_runner", _runner({**OK_RESULT, "files": []}, seen))
    calls = [
        {"name": "run_python", "args": {"code": "print(1)"}, "id": "call-1", "type": "tool_call"},
        {"name": "run_python", "args": {"code": "print(2)"}, "id": "call-2", "type": "tool_call"},
    ]
    use_script(monkeypatch, Script([{"content": "", "tool_calls": calls}, {"content": "Done."}]))
    conversation = Conversation.objects.create(owner=user)

    _, events = stream(auth_client, conversation.id, chat_mode="general")

    assert sorted(run["code"] for run in _code_runs(events)) == ["print(1)", "print(2)"]
    assert sorted(seen) == ["print(1)", "print(2)"]


def test_stale_unbound_files_are_cleaned_up(user):
    import os
    from datetime import timedelta

    from django.utils import timezone

    conversation = Conversation.objects.create(owner=user)
    stale = _stored(user, conversation, "old.png", _png(), "image/png")
    fresh = _stored(user, conversation, "new.png", _png(), "image/png")
    bound = _stored(user, conversation, "kept.png", _png(), "image/png")
    bound.message = Message.objects.create(conversation=conversation, role="assistant")
    bound.save()
    GeneratedFile.objects.filter(id__in=[stale.id, bound.id]).update(
        created_at=timezone.now() - timedelta(days=2)
    )

    assert code_tools.delete_stale_unbound_files() == 1

    assert set(GeneratedFile.objects.values_list("id", flat=True)) == {fresh.id, bound.id}
    assert not os.path.exists(stale.storage_path)


def test_deleting_a_conversation_erases_it_everywhere(auth_client, other_client, user):
    import os

    from django.db import connection

    from chat.models import ChatAttachment

    conversation = Conversation.objects.create(owner=user, title="Budget")
    message = Message.objects.create(conversation=conversation, role="assistant", content="17")
    record = _stored(user, conversation, "chart.png", _png(), "image/png")
    # Stand-in for LangGraph's table (tests never run init_checkpointer).
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE checkpoints (thread_id text, checkpoint_id text)")
        cursor.execute("INSERT INTO checkpoints VALUES (%s, 'a'), ('someone-else', 'b')", [str(conversation.id)])

    # Another user cannot delete it, and learns nothing about it existing.
    assert other_client.delete(f"/api/conversations/{conversation.id}/").status_code == 404
    assert Conversation.objects.filter(id=conversation.id).exists()

    response = auth_client.delete(f"/api/conversations/{conversation.id}/")

    assert response.status_code == 204
    assert not Conversation.objects.filter(id=conversation.id).exists()
    assert not Message.objects.filter(seq=message.seq).exists()
    assert not ChatAttachment.objects.filter(conversation_id=conversation.id).exists()
    assert not os.path.exists(record.storage_path)
    with connection.cursor() as cursor:
        cursor.execute("SELECT thread_id FROM checkpoints")
        assert cursor.fetchall() == [("someone-else",)]
    assert auth_client.get(f"/api/conversations/{conversation.id}/").status_code == 404
