"""A spreadsheet from upload to answers: columns, index, read-only SQL, tools."""
from __future__ import annotations

import json

import pytest
from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import SimpleUploadedFile

from common import fsm
from documents.models import Document
from rag import chunking
from sheets.models import Sheet
from sheets.services import store
from tests.sheet_fixtures import STUDENT_PROPOSAL, FakeProposer, arabic_csv, student_workbook


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.1] * 1024 for _ in texts]


@pytest.fixture
def env(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.CHUNK_CONTEXT_ENABLED = False
    monkeypatch.setattr(chunking, "get_tokenizer", lambda: None)
    monkeypatch.setattr("rag.tasks.get_client", lambda: _FakeEmbedder())
    proposer = FakeProposer(STUDENT_PROPOSAL)
    monkeypatch.setattr("sheets.services.schema._client", lambda: proposer)
    return proposer


def _upload(client, data: bytes, name: str, capture):
    with capture(execute=True):
        response = client.post("/api/documents/", data={"file": SimpleUploadedFile(name, data)})
    assert response.status_code == 201, response.content
    return Document.objects.get(id=response.json()["id"])


def _confirm(client, document, capture, body=None):
    with capture(execute=True):
        response = client.post(
            f"/api/documents/{document.id}/sheets/confirm/",
            data=json.dumps(body or {}), content_type="application/json",
        )
    assert response.status_code == 200, response.content
    document.refresh_from_db()
    return response.json()


@pytest.fixture
def ready_sheet(auth_client, env, django_capture_on_commit_callbacks):
    document = _upload(auth_client, student_workbook(), "grades.xlsx", django_capture_on_commit_callbacks)
    _confirm(auth_client, document, django_capture_on_commit_callbacks)
    assert document.status == fsm.READY
    return document


# ---- upload and the Columns step -------------------------------------------------


@pytest.mark.django_db
def test_an_upload_is_read_and_its_columns_proposed(auth_client, env, django_capture_on_commit_callbacks):
    document = _upload(auth_client, student_workbook(), "grades.xlsx", django_capture_on_commit_callbacks)

    document.refresh_from_db()
    assert document.is_spreadsheet and document.storage_path.endswith("original.xlsx")
    assert document.status == fsm.PREPROCESSED  # waits for the user, no OCR
    assert document.page_count == 1

    response = auth_client.get(f"/api/documents/{document.id}/sheets/")
    [sheet] = response.json()["sheets"]
    assert sheet["schema_status"] == Sheet.PROPOSED and sheet["schema_source"] == "llm"
    assert sheet["row_count"] == 6
    # The model saw headers and samples, never whole rows.
    assert "Grades / Math" in env.prompts[0] and "Ahmed Ali" not in env.prompts[0]
    assert sheet["preview"][0]["text"].startswith("Student: Ahmed Ali (20231001)")


@pytest.mark.django_db
def test_a_failed_proposal_falls_back_to_the_heuristic(auth_client, env, django_capture_on_commit_callbacks):
    env.error = RuntimeError("ollama down")
    document = _upload(auth_client, arabic_csv(), "grades.csv", django_capture_on_commit_callbacks)

    sheet = document.sheets.get()
    assert sheet.schema_status == Sheet.PROPOSED and sheet.schema_source == "heuristic"
    assert "ollama down" in sheet.proposal_error
    assert sheet.proposed_schema["columns"][0]["role"] == "identifier"


@pytest.mark.django_db
def test_preview_shows_cards_for_an_unsaved_schema(auth_client, env, django_capture_on_commit_callbacks):
    document = _upload(auth_client, student_workbook(), "grades.xlsx", django_capture_on_commit_callbacks)
    schema = {**STUDENT_PROPOSAL, "entity": "pupil"}
    response = auth_client.post(
        f"/api/documents/{document.id}/sheets/preview/",
        data=json.dumps({"sheet": 0, "schema": schema}), content_type="application/json",
    )
    assert response.status_code == 200
    card = response.json()["preview"][0]["text"]
    assert card.splitlines()[:2] == ["Pupil: Ahmed Ali (20231001)", "Sheet: Grades 2024 · Row 5"]
    assert "Math: 87" in card and "Physics: ٩٠" in card


@pytest.mark.django_db
def test_confirm_indexes_one_chunk_per_row(ready_sheet):
    from enrichment.models import MetadataRecord
    from rag.models import Chunk

    document = ready_sheet
    assert document.current_revision.source == "sheet_import"
    record = MetadataRecord.objects.get(document=document)
    assert record.doc_type == "spreadsheet" and record.topics == ["student"]

    chunks = list(Chunk.objects.filter(document=document).order_by("chunk_index"))
    assert len(chunks) == 6
    assert chunks[1].meta["sheet"] == "Grades 2024" and chunks[1].meta["row"] == 6
    assert chunks[1].page_start == 1
    # Fill-down was suggested for Section, so Sara inherits Ahmed's section.
    assert "Section: A" in chunks[1].text and chunks[1].text.startswith("Student: Sara Hassan")


@pytest.mark.django_db
def test_spreadsheets_skip_stage_two_and_ocr(ready_sheet, auth_client):
    from rag.tasks import queue_contextualize

    assert queue_contextualize(str(ready_sheet.id), force=True) is False
    response = auth_client.post(
        f"/api/documents/{ready_sheet.id}/ocr/", data={"engines": ["tesseract"]}, content_type="application/json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_editing_columns_after_ready_reindexes(ready_sheet, auth_client, django_capture_on_commit_callbacks):
    from rag.models import Chunk

    sheet = ready_sheet.sheets.get()
    schema = dict(sheet.schema)
    schema["entity"] = "learner"
    _confirm(auth_client, ready_sheet, django_capture_on_commit_callbacks,
             {"sheets": [{"index": 0, "schema": schema}]})

    assert ready_sheet.status == fsm.READY
    assert ready_sheet.revisions.count() == 2
    assert Chunk.objects.get(document=ready_sheet, meta__row=5).text.startswith("Learner: Ahmed Ali")


@pytest.mark.django_db
def test_sheet_endpoints_are_owner_only(ready_sheet, other_client):
    assert other_client.get(f"/api/documents/{ready_sheet.id}/sheets/").status_code == 404
    response = other_client.post(f"/api/documents/{ready_sheet.id}/sheets/confirm/", data={}, content_type="application/json")
    assert response.status_code == 404


# ---- the query store -----------------------------------------------------------


@pytest.mark.django_db
def test_sql_answers_counts_and_averages(ready_sheet):
    path = store.sqlite_path(ready_sheet)
    result = store.run_select(path, "SELECT COUNT(*) AS n FROM grades_2024 WHERE math < 50")
    assert result["rows"] == [[2]]
    result = store.run_select(path, "SELECT class, AVG(physics) FROM grades_2024 GROUP BY class ORDER BY class")
    assert result["rows"] == [["10-A", pytest.approx(69.333333)], ["10-B", pytest.approx(68.333333)]]
    result = store.run_select(path, "SELECT COUNT(*) FROM grades_2024 WHERE passed = 1")
    assert result["rows"] == [[4]]


@pytest.mark.django_db
@pytest.mark.parametrize("sql", [
    "DELETE FROM grades_2024",
    "DROP TABLE grades_2024",
    "INSERT INTO grades_2024 (_row) VALUES (99)",
    "UPDATE grades_2024 SET math = 100",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "PRAGMA table_info(grades_2024)",
    "SELECT load_extension('/tmp/evil')",
    "SELECT 1; DROP TABLE grades_2024",
    "CREATE TABLE t (a)",
])
def test_anything_but_a_read_is_refused(ready_sheet, sql):
    path = store.sqlite_path(ready_sheet)
    with pytest.raises(store.QueryError):
        store.run_select(path, sql)
    assert store.run_select(path, "SELECT COUNT(*) FROM grades_2024")["rows"] == [[6]]


@pytest.mark.django_db
def test_a_runaway_query_is_stopped_and_results_are_capped(ready_sheet):
    path = store.sqlite_path(ready_sheet)
    endless = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    with pytest.raises(store.QueryError, match="longer than"):
        store.run_select(path, endless, timeout_s=0.2)

    many = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n LIMIT 500) SELECT i FROM n"
    result = store.run_select(path, many, max_rows=10)
    assert result["row_count"] == 10 and result["truncated"] is True


# ---- agent tools ---------------------------------------------------------------


def _call(tool, **args):
    return async_to_sync(tool.ainvoke)({"type": "tool_call", "name": tool.name, "id": "t1", "args": args})


@pytest.mark.django_db
def test_describe_lists_columns_with_exact_values(ready_sheet):
    from chat.sheet_tools import describe_spreadsheet

    message = _call(describe_spreadsheet, document_id=str(ready_sheet.id), allowed_doc_ids=[str(ready_sheet.id)])
    described = json.loads(message.content)
    [sheet] = described["sheets"]
    assert sheet["table"] == "grades_2024" and sheet["rows"] == 6
    columns = {c["column"]: c for c in sheet["columns"]}
    assert set(columns["class"]["values"]) == {"10-A", "10-B"}
    assert columns["math"]["range"] == [38, 91]
    assert columns["full_name"]["combined_from"] == ["first_name", "last_name"]


@pytest.mark.django_db
def test_query_returns_rows_and_citable_hits(ready_sheet):
    from chat.sheet_tools import query_spreadsheet

    message = _call(
        query_spreadsheet, document_id=str(ready_sheet.id), allowed_doc_ids=[str(ready_sheet.id)],
        sql="SELECT _row, full_name, math FROM grades_2024 WHERE math < 50 ORDER BY math",
    )
    content = json.loads(message.content)
    assert content["rows"] == [[9, "Yusuf Nasser", 38], [6, "Sara Hassan", 45]]
    assert [h["row"] for h in content["hits"]] == [9, 6]
    assert content["hits"][0]["text"].startswith("Student: Yusuf Nasser")
    assert message.artifact["sql"].startswith("SELECT _row") and message.artifact["ok"] is True


@pytest.mark.django_db
def test_query_tools_respect_the_conversation_scope(ready_sheet):
    from chat.sheet_tools import describe_spreadsheet, query_spreadsheet

    message = _call(query_spreadsheet, document_id=str(ready_sheet.id), allowed_doc_ids=[], sql="SELECT 1")
    assert json.loads(message.content)["ok"] is False
    assert message.artifact["error"].startswith("document is outside")
    message = _call(describe_spreadsheet, document_id=str(ready_sheet.id), allowed_doc_ids=None)
    assert "error" in json.loads(message.content)


@pytest.mark.django_db
def test_a_bad_query_comes_back_as_an_error_the_model_can_fix(ready_sheet):
    from chat.sheet_tools import query_spreadsheet

    message = _call(query_spreadsheet, document_id=str(ready_sheet.id), allowed_doc_ids=[str(ready_sheet.id)],
                    sql="SELECT nope FROM grades_2024")
    assert json.loads(message.content) == {"ok": False, "error": "no such column: nope"}


def test_query_results_feed_citations():
    from langchain_core.messages import ToolMessage

    from chat.graph import _citation, collect

    hit = {"chunk_id": "c1", "document_id": "d1", "document_title": "Grades", "page_start": 1,
           "page_end": 1, "sheet": "Grades 2024", "row": 9, "text": "Student: Yusuf"}
    message = ToolMessage(content=json.dumps({"ok": True, "rows": [], "hits": [hit]}), tool_call_id="t1")
    state = async_to_sync(collect)({"messages": [message], "retrieved": {}, "tool_rounds": 0})
    assert state["retrieved"]["c1"]["row"] == 9
    assert _citation(hit, 1)["row"] == 9 and _citation(hit, 1)["sheet"] == "Grades 2024"


def test_documents_mode_has_the_sheet_tools_and_still_no_code():
    from chat import graph as chat_graph

    tools = chat_graph.build_graph().nodes["tools"].bound.tools_by_name
    assert {"describe_spreadsheet", "query_spreadsheet"} <= set(tools)
    assert "run_python" not in tools


@pytest.mark.django_db
def test_a_spreadsheet_can_be_named_by_its_title(ready_sheet, auth_client, django_capture_on_commit_callbacks):
    # The model often only knows the title from the conversation's scope note.
    from chat.sheet_tools import describe_spreadsheet

    allowed = [str(ready_sheet.id)]
    described = json.loads(_call(describe_spreadsheet, document_id="grades.xlsx", allowed_doc_ids=allowed).content)
    assert described["document_id"] == str(ready_sheet.id)

    # With two spreadsheets in scope, an unknown name gets the list to choose from.
    other = _upload(auth_client, arabic_csv(), "marks.csv", django_capture_on_commit_callbacks)
    _confirm(auth_client, other, django_capture_on_commit_callbacks)
    allowed.append(str(other.id))
    error = json.loads(_call(describe_spreadsheet, document_id="results", allowed_doc_ids=allowed).content)["error"]
    assert str(ready_sheet.id) in error and str(other.id) in error


def test_a_marker_that_is_not_a_chunk_id_never_reaches_the_answer():
    from chat.graph import resolve_citations

    text, citations, mode = resolve_citations("Average is 69.33 [[cite:_row]].", {})
    assert text == "Average is 69.33." and citations == [] and mode == "none"
