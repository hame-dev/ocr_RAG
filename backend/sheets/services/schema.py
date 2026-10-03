"""What each column means: proposed by the LLM, repaired here, confirmed by the user.

A schema is a plain dict (stored on Sheet.schema):

    {"entity": "student", "description": "...", "title_template": "{full_name} ({student_id})",
     "columns": [{"source": "<header>", "key": "student_id", "label": "Student ID",
                  "type": "id", "role": "identifier", "description": "", "fill_down": false}],
     "combine": [{"key": "full_name", "label": "Full name", "from": ["first_name", "last_name"], "sep": " "}]}

`normalize` is the single gate for both the model's proposal and the user's
edits: whatever comes in, what comes out references only real columns, has
unique SQL-safe keys and a template that renders.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from django.conf import settings
from pydantic import BaseModel, ValidationError

from sheets.services.profile import TYPES

logger = logging.getLogger(__name__)

ROLES = ("identifier", "title", "attribute", "ignore")
PROMPT_VERSION = "sheets-v1"
MAX_LABEL = 80
MAX_DESCRIPTION = 300
MAX_ENTITY = 40
# Keys a model would write unquoted in SQL; a column named "order" or "group"
# makes every query that mentions it fail.
SQL_RESERVED = {
    "select", "from", "where", "group", "order", "by", "having", "limit", "offset", "table",
    "index", "join", "on", "and", "or", "not", "null", "is", "in", "as", "case", "when",
    "then", "else", "end", "values", "insert", "update", "delete", "create", "drop", "union",
    "all", "distinct", "like", "between", "exists", "default", "check", "primary", "key",
    "references", "transaction", "to", "with", "_row",
}
_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")


class _Column(BaseModel):
    source: str
    key: str
    label: str
    type: Literal[TYPES]  # type: ignore[valid-type]
    role: Literal[ROLES]  # type: ignore[valid-type]
    description: str


class _Combine(BaseModel):
    key: str
    label: str
    sources: list[str]
    sep: str


class _Proposal(BaseModel):
    entity: str
    entity_ar: str
    description: str
    title_template: str
    columns: list[_Column]
    combine: list[_Combine]


PROPOSAL_PROMPT = """You are preparing a spreadsheet so that it can be searched and queried.
Each row of the sheet is one record. Decide what each column means.

File: {filename}
Sheet: {sheet} ({rows} rows)
Columns (header: inferred type, empty share, distinct values, sample values):
{columns}

Return JSON with:
- entity: what ONE row describes, a singular English noun ("student", "employee", "invoice", "record").
- entity_ar: the same noun in Arabic.
- description: one sentence describing the whole sheet, mentioning the row count.
- columns: one entry for EVERY column above, in the same order:
  - source: the header exactly as given.
  - key: a short English snake_case name for SQL (student_id, full_name, math_grade).
    Translate Arabic headers into English for the key.
  - label: a short human label, in the header's own language.
  - type: one of text, integer, number, date, boolean, category, id, email, phone.
    Keep the inferred type unless the header or the samples clearly say otherwise.
  - role: "identifier" for a column that uniquely identifies a row (an ID or a code),
    "title" for the column(s) that name the row (a person's or item's name),
    "ignore" for empty, meaningless or purely positional columns (a running serial number),
    otherwise "attribute".
  - description: a few words on what the column holds; empty if obvious.
- combine: only when one value is split across columns, e.g. first and last name.
  Each has key, label, sources (the keys to join, in order) and sep (usually " ").
  Otherwise an empty list.
- title_template: how to name a row using {{key}} placeholders, e.g. "{{full_name}} ({{student_id}})".
  Use the title and identifier columns. Empty if the sheet has no name-like column."""


def _client():
    from common.ollama import get_client

    return get_client()


def proposal_json_schema() -> dict:
    from enrichment.schemas import _force_all_required, inline_defs

    return _force_all_required(inline_defs(_Proposal.model_json_schema()))


def _column_line(column: dict) -> str:
    samples = ", ".join(repr(s[:40]) for s in column.get("samples", [])[:6])
    return (
        f"- {column['header']!r}: {column['type']}, empty {round(column['null_ratio'] * 100)}%, "
        f"{column['distinct']} distinct, samples [{samples}]"
    )


def propose(filename: str, sheet_name: str, row_count: int, profile: list[dict]) -> dict:
    """The model's schema, normalized. Raises on failure (callers fall back)."""
    prompt = PROPOSAL_PROMPT.format(
        filename=filename, sheet=sheet_name, rows=row_count,
        columns="\n".join(_column_line(c) for c in profile),
    )
    parsed, raw = _client().generate_json(
        settings.LLM_MODEL, prompt, proposal_json_schema(), timeout=settings.SHEET_PROPOSAL_TIMEOUT_S,
    )
    if parsed is None:
        raise ValueError(f"schema proposal was not JSON: {raw[:200]}")
    try:
        proposal = _Proposal.model_validate(parsed)
    except ValidationError as exc:
        # Partially usable output still beats the heuristic: normalize repairs it.
        logger.warning("schema proposal failed validation: %s", exc)
        return normalize(parsed if isinstance(parsed, dict) else {}, profile)
    data = proposal.model_dump()
    for item in data["combine"]:
        item["from"] = item.pop("sources")
    return normalize(data, profile)


def heuristic_schema(profile: list[dict], *, sheet_name: str = "", row_count: int = 0) -> dict:
    """A usable schema without the LLM: headers as labels, inferred types."""
    columns = []
    identifier_done = title_done = False
    for column in profile:
        role = "attribute"
        if column["type"] == "id" and not identifier_done:
            role, identifier_done = "identifier", True
        elif (
            not title_done and column["type"] == "text"
            and re.search(r"name|اسم", column["header"], re.IGNORECASE)
        ):
            role, title_done = "title", True
        columns.append({"source": column["header"], "label": column["header"], "type": column["type"], "role": role})
    described = f"{row_count} rows from {sheet_name}" if sheet_name else f"{row_count} rows"
    return normalize({"entity": "record", "description": described, "columns": columns}, profile)


# ---- normalization ------------------------------------------------------------


def slug(text: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:48].strip("_")
    if not key or key[0].isdigit():
        return ""
    return key


def _text(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def normalize(raw: dict, profile: list[dict]) -> dict:
    """A valid schema for `profile`, taking whatever is usable from `raw`."""
    raw = raw if isinstance(raw, dict) else {}
    by_source: dict[str, dict] = {}
    for item in raw.get("columns") or []:
        if isinstance(item, dict) and isinstance(item.get("source"), str):
            by_source.setdefault(item["source"].strip().casefold(), item)

    used: set[str] = set()

    def unique_key(candidate: str, position: int) -> str:
        key = slug(candidate) or f"col_{position + 1}"
        if key in SQL_RESERVED:
            key = f"{key}_"
        base, n = key, 2
        while key in used:
            key, n = f"{base}_{n}", n + 1
        used.add(key)
        return key

    columns = []
    for position, column in enumerate(profile):
        header = column["header"]
        item = by_source.get(header.strip().casefold(), {})
        column_type = item.get("type") if item.get("type") in TYPES else column["type"]
        role = item.get("role") if item.get("role") in ROLES else "attribute"
        fill_down = item.get("fill_down")
        columns.append({
            "source": header,
            "key": unique_key(str(item.get("key") or header), position),
            "label": _text(item.get("label"), MAX_LABEL) or header[:MAX_LABEL],
            "type": column_type,
            "role": role,
            "description": _text(item.get("description"), MAX_DESCRIPTION),
            "fill_down": bool(fill_down) if fill_down is not None else bool(column.get("fill_down_suggested")),
        })

    column_keys = {c["key"] for c in columns}
    combine = []
    for item in raw.get("combine") or []:
        if not isinstance(item, dict):
            continue
        sources = [s for s in (item.get("from") or item.get("sources") or []) if s in column_keys]
        if len(sources) < 2:
            continue
        combine.append({
            "key": unique_key(str(item.get("key") or "_".join(sources)), len(columns) + len(combine)),
            "label": _text(item.get("label"), MAX_LABEL) or " ".join(sources),
            "from": sources,
            "sep": str(item.get("sep") if item.get("sep") is not None else " ")[:5],
        })

    known = column_keys | {c["key"] for c in combine}
    template = _text(raw.get("title_template"), 200)
    placeholders = _PLACEHOLDER.findall(template)
    if not placeholders or any(p not in known for p in placeholders):
        template = default_template(columns, combine)

    return {
        "entity": _text(raw.get("entity"), MAX_ENTITY) or "record",
        "entity_ar": _text(raw.get("entity_ar"), MAX_ENTITY),
        "description": _text(raw.get("description"), MAX_DESCRIPTION),
        "title_template": template,
        "columns": columns,
        "combine": combine,
        "prompt_version": PROMPT_VERSION,
    }


def default_template(columns: list[dict], combine: list[dict]) -> str:
    combined_sources = {s for c in combine for s in c["from"]}
    titles = [c["key"] for c in combine] + [
        c["key"] for c in columns if c["role"] == "title" and c["key"] not in combined_sources
    ]
    identifier = next((c["key"] for c in columns if c["role"] == "identifier"), None)
    if titles:
        template = " ".join(f"{{{k}}}" for k in titles)
        return f"{template} ({{{identifier}}})" if identifier else template
    return f"{{{identifier}}}" if identifier else ""
