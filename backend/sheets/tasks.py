from __future__ import annotations

import logging

from celery import shared_task

from documents.models import Document

logger = logging.getLogger(__name__)


@shared_task(name="sheets.tasks.propose_schema")
def propose_schema(document_id: str, sheet_index: int | None = None):
    """Suggest a schema for every sheet (or one). Never raises: a failed
    proposal falls back to the heuristic schema inside `propose`."""
    from sheets.services.pipeline import propose

    document = Document.objects.filter(id=document_id).first()
    if document is None:
        return {"error": "no such document"}
    sheets = document.sheets.all()
    if sheet_index is not None:
        sheets = sheets.filter(index=sheet_index)
    done = 0
    for sheet in sheets:
        try:
            propose(sheet)
            done += 1
        except Exception:
            logger.exception("proposing a schema for sheet %s of %s failed", sheet.index, document_id)
    return {"sheets": done}
