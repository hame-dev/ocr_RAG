"""Rows as records: typed values for SQLite, and a "card" of text for search.

A card is what gets embedded, keyword-indexed and quoted in a citation:

    Student: Ahmed Ali (20231045)
    Sheet: Grades 2024 · Row 12
    Class: 10-B
    Math: 87

The same function renders the Columns step's preview, the revision text and
the chunks, so what the user previews is exactly what gets indexed.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterator

from sheets.services.profile import display, typed_value

_EMPTY_BRACKETS = re.compile(r"\(\s*\)|\[\s*\]")


def iter_records(rows: list, schema: dict) -> Iterator[tuple[int, dict, dict]]:
    """(row_number, {key: display text}, {key: typed value}) per row.

    Applies fill-down and combined columns. Ignored columns are kept in the
    typed values (they still exist in the table) but not in the card.
    """
    columns = schema.get("columns") or []
    last: dict[str, object] = {}
    for row_number, values in rows:
        shown: dict[str, str] = {}
        typed: dict[str, object] = {}
        for position, column in enumerate(columns):
            value = values[position] if position < len(values) else None
            key = column["key"]
            if column.get("fill_down"):
                if value is None or value == "":
                    value = last.get(key)
                else:
                    last[key] = value
            shown[key] = display(value)
            typed[key] = typed_value(value, column["type"])
        for item in schema.get("combine") or []:
            joined = (item.get("sep") or "").join(shown[k] for k in item["from"] if shown.get(k))
            shown[item["key"]] = joined
            typed[item["key"]] = joined or None
        yield row_number, shown, typed


def row_title(schema: dict, shown: dict) -> str:
    template = schema.get("title_template") or ""
    if not template:
        return ""
    title = template.format_map(defaultdict(str, shown))
    title = _EMPTY_BRACKETS.sub("", title)
    return re.sub(r"\s+", " ", title).strip()


def render_card(sheet_name: str, schema: dict, row_number: int, shown: dict) -> str:
    entity = (schema.get("entity") or "record").strip()
    title = row_title(schema, shown)
    lines = [f"{entity[:1].upper()}{entity[1:]}: {title}" if title else f"{entity[:1].upper()}{entity[1:]}"]
    lines.append(f"Sheet: {sheet_name} · Row {row_number}")

    in_title = set(re.findall(r"\{([a-z0-9_]+)\}", schema.get("title_template") or ""))
    combined_sources = {s for c in schema.get("combine") or [] for s in c["from"]}
    for item in schema.get("combine") or []:
        if item["key"] not in in_title and shown.get(item["key"]):
            lines.append(f"{item['label']}: {shown[item['key']]}")
    for column in schema.get("columns") or []:
        key = column["key"]
        if column["role"] == "ignore" or not shown.get(key):
            continue
        # A name already shown in the title line, or folded into a combined
        # value, would only repeat itself.
        if key in in_title and column["role"] in ("title", "identifier"):
            continue
        if key in combined_sources and any(c["key"] in in_title for c in schema.get("combine") or []):
            continue
        lines.append(f"{column['label']}: {shown[key]}")
    return "\n".join(lines)


def sheet_cards(sheet) -> Iterator[tuple[int, str]]:
    schema = sheet.active_schema
    for row_number, shown, _typed in iter_records(sheet.rows, schema):
        yield row_number, render_card(sheet.name, schema, row_number, shown)


def preview_cards(sheet, schema: dict, limit: int = 3) -> list[dict]:
    cards = []
    for row_number, shown, _typed in iter_records(sheet.rows[:limit], schema):
        cards.append({"row": row_number, "text": render_card(sheet.name, schema, row_number, shown)})
    return cards


def revision_text(sheets) -> str:
    """The document's text: every card, a page (\\f) per sheet."""
    return "\f".join("\n\n".join(card for _, card in sheet_cards(sheet)) for sheet in sheets)


def sheet_drafts(document) -> list:
    """One chunk per row, for rag.tasks.index_document."""
    from common.arabic import detect_lang
    from rag.chunking import ChunkDraft, count_tokens

    drafts = []
    for sheet in document.sheets.all():
        for row_number, card in sheet_cards(sheet):
            drafts.append(ChunkDraft(
                text=card,
                chunk_index=len(drafts),
                page_start=sheet.index + 1,
                page_end=sheet.index + 1,
                section_path=sheet.name,
                lang=detect_lang(card),
                token_count=count_tokens(card),
                char_count=len(card),
                meta={"sheet": sheet.name, "row": row_number},
            ))
    return drafts
