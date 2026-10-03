"""Agent tools for spreadsheet documents: see the tables, then query them.

Search finds a record by name ("tell me about Ahmed Ali"); these answer what
search cannot: counts, sums, averages, rankings and filters over every row.
Both are read-only and scoped exactly like the other Documents tools. The SQL
runs against the document's own read-only SQLite file (sheets/services/store.py),
so nothing here executes code.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Annotated

from asgiref.sync import sync_to_async
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

logger = logging.getLogger(__name__)

# Rows the model reads back; the UI panel shows fewer.
MODEL_ROWS = 50
PANEL_ROWS = 20
# Rows of a result that come back citable, as [[cite:<chunk_id>]].
CITABLE_ROWS = 15
CARD_CHARS = 240


def _resolve(reference: str, allowed_doc_ids) -> tuple[object | None, str]:
    """The spreadsheet `reference` names, among the conversation's documents.

    The model often knows a document only by the title in its scope note
    ("grades.xlsx"), so a title or file name is accepted as well as an id.
    With a single spreadsheet in scope, that one is meant. Returns
    (document, "") or (None, a message that lists what is available).
    """
    from documents.models import Document
    from sheets.services.parse import SPREADSHEET_MIMES

    if not allowed_doc_ids:
        return None, "document is outside this conversation's selected sources"
    sheets = list(Document.objects.filter(
        id__in=allowed_doc_ids, mime_type__in=SPREADSHEET_MIMES, sheets__schema_status="confirmed",
    ).distinct())
    reference = (reference or "").strip()
    wanted = reference.casefold()
    matches = [
        d for d in sheets
        if str(d.id) == reference or wanted in {d.display_title.casefold(), d.original_filename.casefold()}
    ]
    if len(matches) == 1:
        return matches[0], ""
    if not matches and len(sheets) == 1:
        return sheets[0], ""
    if not sheets:
        return None, "there is no indexed spreadsheet among this conversation's documents"
    available = "; ".join(f"{d.id} ({d.display_title})" for d in sheets[:20])
    return None, f"no single spreadsheet matches {reference!r}; use one of these document ids: {available}"


def _column_entry(column: dict, profile: dict | None) -> dict:
    entry = {
        "column": column["key"],
        "label": column["label"],
        "type": column["type"],
    }
    if column.get("role") and column["role"] != "attribute":
        entry["role"] = column["role"]
    if column.get("description"):
        entry["description"] = column["description"]
    profile = profile or {}
    if "options" in profile and column["type"] in ("category", "boolean", "text"):
        # Exact spellings, so a filter matches what is really stored.
        entry["values"] = profile["options"]
    elif "min" in profile:
        entry["range"] = [profile["min"], profile["max"]]
        if "mean" in profile:
            entry["mean"] = profile["mean"]
    elif profile.get("samples"):
        entry["examples"] = profile["samples"][:3]
    if profile.get("null_ratio"):
        entry["empty_share"] = profile["null_ratio"]
    return entry


def describe(document) -> dict:
    sheets = []
    for sheet in document.sheets.all():
        schema = sheet.schema
        if not schema:
            continue
        profiles = {p["header"]: p for p in sheet.profile}
        columns = [_column_entry(c, profiles.get(c["source"])) for c in schema["columns"]]
        columns += [
            {"column": c["key"], "label": c["label"], "type": "text", "combined_from": c["from"]}
            for c in schema.get("combine") or []
        ]
        sheets.append({
            "table": sheet.table_name,
            "sheet": sheet.name,
            "rows": sheet.row_count,
            "each_row_is": schema.get("entity") or "record",
            "description": schema.get("description") or "",
            "columns": columns,
        })
    return {
        "document_id": str(document.id),
        "title": document.display_title,
        "sheets": sheets,
        "how_to_query": (
            "Use query_spreadsheet with one SQLite SELECT on these tables. Every table has a "
            "_row column (the spreadsheet row number); select it to be able to cite rows. "
            "Dates are text YYYY-MM-DD (use strftime); booleans are 1/0."
        ),
    }


@tool
async def describe_spreadsheet(
    document_id: str,
    allowed_doc_ids: Annotated[list[str] | None, InjectedState("doc_ids")],
) -> dict:
    """Show a spreadsheet document's tables: column names, types, value ranges
    and the exact category values.

    Call this before query_spreadsheet, so the SQL uses real column names and
    real values. Works only on documents whose doc_type is "spreadsheet".

    Args:
        document_id: The spreadsheet's document id, or its title.
    """

    @sync_to_async(thread_sensitive=True)
    def _load():
        document, error = _resolve(document_id, allowed_doc_ids)
        return describe(document) if document else {"error": error}

    return await _load()


def _citable_hits(document, sql: str, columns: list[str], rows: list[list]) -> list[dict]:
    """Search-hit shaped entries for the result's rows, so they can be cited."""
    from rag.models import Chunk

    if "_row" not in columns or not rows:
        return []
    sheets = list(document.sheets.all())
    if len(sheets) == 1:
        sheet = sheets[0]
    else:
        # With several sheets, _row is only meaningful when one table is queried.
        named = [s for s in sheets if re.search(rf'\b"?{re.escape(s.table_name)}"?\b', sql, re.IGNORECASE)]
        if len(named) != 1:
            return []
        sheet = named[0]
    position = columns.index("_row")
    numbers = [row[position] for row in rows[:CITABLE_ROWS] if isinstance(row[position], int)]
    chunks = Chunk.objects.filter(document=document, meta__sheet=sheet.name, meta__row__in=numbers)
    by_row = {c.meta.get("row"): c for c in chunks}
    hits = []
    for number in numbers:
        chunk = by_row.get(number)
        if chunk is None:
            continue
        hits.append({
            "chunk_id": str(chunk.id),
            "document_id": str(document.id),
            "document_title": document.display_title,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "sheet": sheet.name,
            "row": number,
            "text": chunk.text[:CARD_CHARS],
        })
    return hits


def table_text(columns: list[str], rows: list[list], *, limit: int = PANEL_ROWS) -> str:
    """A plain-text table for the UI panel."""
    shown = [[("" if v is None else str(v)) for v in row] for row in rows[:limit]]
    widths = [min(30, max([len(c)] + [len(r[i]) for r in shown])) for i, c in enumerate(columns)]

    def line(values):
        return " | ".join(str(v)[:30].ljust(w) for v, w in zip(values, widths)).rstrip()

    lines = [line(columns), "-+-".join("-" * w for w in widths)] + [line(r) for r in shown]
    if len(rows) > limit:
        lines.append(f"… {len(rows) - limit} more rows")
    return "\n".join(lines)


@tool(response_format="content_and_artifact")
async def query_spreadsheet(
    document_id: str,
    sql: str,
    allowed_doc_ids: Annotated[list[str] | None, InjectedState("doc_ids")],
) -> tuple[str, dict]:
    """Run one read-only SQLite SELECT on a spreadsheet document's tables.

    Use it for every count, sum, average, minimum/maximum, ranking, grouping or
    filter over a spreadsheet; never work these out from search results. Call
    describe_spreadsheet first for the table and column names.

    Tips: compare text with LOWER(col) = LOWER('value'); if a filter returns no
    rows, retry once with LIKE '%value%'; group dates with strftime('%Y-%m', col);
    guard divisions with NULLIF(x, 0). Select _row to get rows you can cite with
    [[cite:<chunk_id>]] (returned under "hits").

    Args:
        document_id: The spreadsheet's document id, or its title.
        sql: A single SELECT statement (WITH ... SELECT is fine).
    """
    artifact: dict = {"sql": sql, "ok": False, "columns": [], "rows": [], "row_count": 0,
                      "truncated": False, "error": "", "duration_ms": None}

    @sync_to_async(thread_sensitive=True)
    def _run():
        from sheets.services.store import QueryError, run_select, sqlite_path

        document, error = _resolve(document_id, allowed_doc_ids)
        if document is None:
            return {"error": error}
        try:
            result = run_select(sqlite_path(document), sql)
        except QueryError as exc:
            return {"error": str(exc)}
        result["hits"] = _citable_hits(document, sql, result["columns"], result["rows"])
        result["title"] = document.display_title
        return result

    try:
        result = await _run()
    except Exception:
        logger.exception("query_spreadsheet failed")
        result = {"error": "the query could not be run"}

    if "error" in result:
        artifact["error"] = result["error"]
        return json.dumps({"ok": False, "error": result["error"]}, ensure_ascii=False), artifact

    artifact.update(
        ok=True, columns=result["columns"], rows=result["rows"][:PANEL_ROWS],
        row_count=result["row_count"], truncated=result["truncated"], duration_ms=result["duration_ms"],
    )
    content = {
        "ok": True,
        "columns": result["columns"],
        "rows": result["rows"][:MODEL_ROWS],
        "row_count": result["row_count"],
        "more_rows": result["truncated"] or result["row_count"] > MODEL_ROWS,
        "hits": result["hits"],
    }
    return json.dumps(content, ensure_ascii=False, default=str), artifact


SHEET_TOOLS = [describe_spreadsheet, query_spreadsheet]
