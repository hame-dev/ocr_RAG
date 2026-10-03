"""A spreadsheet's way through the document lifecycle.

    preprocess  parse the file, profile each column            (UPLOADED → PREPROCESSED)
    propose     LLM suggests what each column means            (stays PREPROCESSED)
    confirm     the user's schema becomes the revision text,
                the SQLite tables and the metadata record      (→ TEXT_FINALIZED → ENRICHED)
    index       one chunk per row (rag.tasks.index_document)   (→ INDEXED → READY)
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common import fsm
from common.arabic import detect_lang
from documents.models import Document
from sheets.models import Sheet
from sheets.services import parse, schema as schema_service
from sheets.services.cards import revision_text
from sheets.services.profile import profile_sheet
from sheets.services.store import build_sqlite

logger = logging.getLogger(__name__)


def is_spreadsheet(document: Document) -> bool:
    return document.mime_type in parse.SPREADSHEET_MIMES


def _table_names(names: list[str]) -> list[str]:
    used: set[str] = set()
    tables = []
    for position, name in enumerate(names):
        table = schema_service.slug(name) or f"sheet_{position + 1}"
        if table in schema_service.SQL_RESERVED or table.startswith("sqlite"):
            table = f"sheet_{table}"
        base, n = table, 2
        while table in used:
            table, n = f"{base}_{n}", n + 1
        used.add(table)
        tables.append(table)
    return tables


def _save_sheet(document: Document, parsed: parse.ParsedSheet, table: str) -> Sheet:
    headers = parsed.headers
    rows = [[n, values] for n, values in parsed.rows]
    sheet, _ = Sheet.objects.update_or_create(
        document=document,
        index=parsed.index,
        defaults={
            "name": parsed.name,
            "table_name": table,
            "header_row": parsed.header_row,
            "header_rows": parsed.header_rows,
            "row_count": len(rows),
            "col_count": len(headers),
            "headers": headers,
            "rows": rows,
            "profile": profile_sheet(headers, rows),
            "proposed_schema": {},
            "schema": {},
            "schema_status": Sheet.PENDING,
            "schema_source": "",
            "proposal_error": "",
            "confirmed_at": None,
        },
    )
    return sheet


def analyze(document: Document) -> list[Sheet]:
    """Parse and profile the file. Replaces any earlier parse of it."""
    parsed = parse.read_file(
        document.storage_path, document.mime_type,
        max_rows=settings.MAX_SHEET_ROWS, max_cols=settings.MAX_SHEET_COLS,
    )
    tables = _table_names([p.name for p in parsed])
    with transaction.atomic():
        document.sheets.exclude(index__in=[p.index for p in parsed]).delete()
        sheets = [_save_sheet(document, p, t) for p, t in zip(parsed, tables)]
        sample = " ".join(
            " ".join(s.headers) + " " + " ".join(" ".join(c["samples"][:3]) for c in s.profile)
            for s in sheets
        )
        lang = detect_lang(sample)
        document.page_count = len(sheets)
        document.is_digital_pdf = None
        document.detected_languages = ["ar", "en"] if lang == "mixed" else [lang]
        document.digital_text_report = {
            "reason": "spreadsheet",
            "sheets": [{"name": s.name, "rows": s.row_count, "columns": s.col_count} for s in sheets],
        }
        document.save(update_fields=[
            "page_count", "is_digital_pdf", "detected_languages", "digital_text_report", "updated_at",
        ])
    return sheets


def reprofile(sheet: Sheet, header_row: int | None) -> Sheet:
    """Re-read one sheet with the header on another row (None: no header)."""
    document = sheet.document
    parsed = parse.read_file(
        document.storage_path, document.mime_type,
        max_rows=settings.MAX_SHEET_ROWS, max_cols=settings.MAX_SHEET_COLS,
        header_overrides={sheet.index: header_row},
    )
    match = next((p for p in parsed if p.index == sheet.index and p.name == sheet.name), None)
    if match is None:
        raise parse.SpreadsheetError("that header row leaves no data rows")
    return _save_sheet(document, match, sheet.table_name)


def propose(sheet: Sheet) -> Sheet:
    """Ask the LLM; fall back to the heuristic so the user is never stuck."""
    Sheet.objects.filter(id=sheet.id).update(schema_status=Sheet.PROPOSING)
    document = sheet.document
    try:
        proposed = schema_service.propose(document.original_filename, sheet.name, sheet.row_count, sheet.profile)
        source, error = "llm", ""
    except Exception as exc:
        logger.warning("schema proposal failed for sheet %s of %s", sheet.index, document.id, exc_info=True)
        proposed = schema_service.heuristic_schema(sheet.profile, sheet_name=sheet.name, row_count=sheet.row_count)
        source, error = "heuristic", str(exc)[:500]
    sheet.proposed_schema = proposed
    sheet.schema_source = source
    sheet.proposal_error = error
    sheet.schema_status = Sheet.CONFIRMED if sheet.schema else Sheet.PROPOSED
    sheet.save(update_fields=["proposed_schema", "schema_source", "proposal_error", "schema_status", "updated_at"])
    fsm.record_event(document, "sheet_schema_proposed", {"sheet": sheet.index, "source": source})
    return sheet


def can_confirm(document: Document) -> bool:
    return document.status == fsm.TEXT_FINALIZED or fsm.can_transition(document.status, fsm.TEXT_FINALIZED)


def confirm(document: Document, schemas: dict[int, dict], *, actor: str = "user") -> None:
    """Adopt the schemas (normalized), then build text, tables and metadata.

    Queues indexing once the transaction commits.
    """
    from documents.views import _next_revision

    sheets = list(document.sheets.all())
    now = timezone.now()
    for sheet in sheets:
        raw = schemas.get(sheet.index) or sheet.schema or sheet.proposed_schema
        if not raw:
            raw = schema_service.heuristic_schema(sheet.profile, sheet_name=sheet.name, row_count=sheet.row_count)
        sheet.schema = schema_service.normalize(raw, sheet.profile)
        sheet.schema_status = Sheet.CONFIRMED
        sheet.confirmed_at = now
        sheet.save(update_fields=["schema", "schema_status", "confirmed_at", "updated_at"])

    build_sqlite(document, sheets)
    revision = _next_revision(
        document, text=revision_text(sheets), source="sheet_import",
        note=f"columns confirmed for {len(sheets)} sheet(s)",
    )
    with transaction.atomic():
        document.revisions.update(is_final=False)
        revision.is_final = True
        revision.save(update_fields=["is_final"])
        fsm.transition_to(
            document, fsm.TEXT_FINALIZED, actor=actor, payload={"revision_no": revision.revision_no},
        )
    write_metadata(document, auto_index=True)


def write_metadata(document: Document, *, auto_index: bool = True) -> None:
    """The metadata record, from the confirmed schema; no LLM call.

    Running the extractor over thousands of record cards would be slow and
    would summarize a sample, not the sheet.
    """
    from enrichment.models import MetadataRecord
    from enrichment.tasks import HUMAN_EDITABLE_FIELDS

    if document.status in (fsm.TEXT_FINALIZED, fsm.ENRICHED, fsm.READY):
        try:
            fsm.transition_to(document, fsm.ENRICHING)
        except fsm.InvalidTransition:
            pass

    sheets = list(document.sheets.all())
    total_rows = sum(s.row_count for s in sheets)
    entities = sorted({(s.schema.get("entity") or "record") for s in sheets})
    descriptions = [s.schema.get("description") for s in sheets if s.schema.get("description")]
    summary = " ".join(descriptions) or f"Spreadsheet with {total_rows} rows in {len(sheets)} sheet(s)."
    labels = list(dict.fromkeys(
        c["label"] for s in sheets for c in s.schema.get("columns", []) if c["role"] != "ignore"
    ))
    lang = document.detected_languages[0] if len(document.detected_languages) == 1 else "mixed"
    title = document.title or document.original_filename.rsplit(".", 1)[0]
    defaults = {
        "doc_type": "spreadsheet",
        "title": title[:1024],
        "primary_language": lang,
        "languages": document.detected_languages,
        "summary_short": summary[:1000],
        "summary_long": "\n".join(
            f"{s.name}: {s.row_count} rows; columns: {', '.join(c['label'] for c in s.schema.get('columns', []) if c['role'] != 'ignore')}"
            for s in sheets
        ),
        "keywords": [label[:96] for label in labels[:30]],
        "topics": [e[:96] for e in entities],
        "provenance": {
            "ocr_engine": "spreadsheet",
            "page_count": document.page_count,
            "revision_no": document.current_revision.revision_no if document.current_revision else None,
            "revision_source": "sheet_import",
        },
        "custom_fields": {
            "sheets": [
                {"name": s.name, "table": s.table_name, "rows": s.row_count, "entity": s.schema.get("entity", "")}
                for s in sheets
            ],
        },
        "model_id": "",
        "prompt_version": schema_service.PROMPT_VERSION,
        "is_partial": False,
        "quality_flags": [],
    }
    if MetadataRecord.objects.filter(document=document, human_edited=True).exists():
        defaults = {k: v for k, v in defaults.items() if k not in HUMAN_EDITABLE_FIELDS}
    MetadataRecord.objects.update_or_create(document=document, defaults=defaults)

    try:
        fsm.transition_to(document, fsm.ENRICHED, payload={"is_partial": False})
    except fsm.InvalidTransition:
        pass
    fsm.record_event(document, "enrichment_done", {"doc_type": "spreadsheet", "is_partial": False, "attempts": 0})

    if auto_index:
        from rag.tasks import index_document

        document_id = str(document.id)
        transaction.on_commit(lambda: index_document.delay(document_id))
