from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

from common import fsm
from documents.models import Document
from enrichment import extractor
from enrichment.models import ExtractionPlan, MetadataRecord

logger = logging.getLogger(__name__)

# The fields MetadataPatchSerializer lets a person edit (plus the date derived
# from `dates`, and the model's raw view of them in `core`).
HUMAN_EDITABLE_FIELDS = {
    "doc_type", "title", "title_translit", "primary_language", "languages",
    "summary_short", "summary_long", "keywords", "topics", "entities", "dates",
    "identifiers", "custom_fields", "document_date", "core",
}


@shared_task(name="enrichment.tasks.plan_extraction")
def plan_extraction(document_id: str):
    """Ask the LLM what is extractable from this document."""
    document = Document.objects.select_related("current_revision").get(id=document_id)
    revision = document.current_revision
    if not revision or not revision.text.strip():
        return {"error": "no finalized text"}

    if document.is_spreadsheet:
        # Metadata comes from the confirmed column schema, not the extractor.
        from sheets.services.pipeline import write_metadata

        write_metadata(document, auto_index=auto_index)
        return {"doc_type": "spreadsheet", "is_partial": False}

    plan_row, _ = ExtractionPlan.objects.get_or_create(document=document)
    plan_row.status = "running"
    plan_row.save(update_fields=["status"])
    fsm.record_event(document, "extraction_plan_started", {})

    result = extractor.plan_fields(revision.text)

    plan_row.detected_doc_type = result.get("detected_doc_type", "")[:64]
    plan_row.proposed_fields = result.get("proposed_fields", [])
    plan_row.error_message = result.get("error", "")
    plan_row.status = "failed" if result.get("error") else "ready"

    # In auto mode we pre-accept the confident proposals so the user can just
    # click through; in advanced mode their declared fields are merged in.
    plan_row.accepted_fields = extractor.merge_required_fields(
        plan_row.proposed_fields,
        document.required_fields if document.metadata_mode == "advanced" else [],
    )
    plan_row.save()

    fsm.record_event(
        document,
        "extraction_plan_ready",
        {
            "detected_doc_type": plan_row.detected_doc_type,
            "proposed": len(plan_row.proposed_fields),
            "accepted": len(plan_row.accepted_fields),
        },
    )
    return {"proposed": len(plan_row.proposed_fields)}


@shared_task(name="enrichment.tasks.enrich_document")
def enrich_document(document_id: str, auto_index: bool = True):
    """Produce the standardized metadata record. Never hard-fails."""
    document = Document.objects.select_related("current_revision").get(id=document_id)
    revision = document.current_revision

    if not revision or not revision.text.strip():
        return {"error": "no finalized text"}

    if document.status in (fsm.TEXT_FINALIZED, fsm.ENRICHED, fsm.READY):
        try:
            fsm.transition_to(document, fsm.ENRICHING)
        except fsm.InvalidTransition:
            pass

    plan_row = getattr(document, "extraction_plan", None)
    if plan_row is None or not plan_row.proposed_fields:
        plan_extraction(document_id)
        document.refresh_from_db()
        plan_row = getattr(document, "extraction_plan", None)

    accepted = plan_row.accepted_fields if plan_row else []

    result = extractor.extract(revision.text, accepted)
    metadata = result["metadata"]

    best_run = (
        document.ocr_runs.filter(status="succeeded").order_by("gibberish_score").first()
    )
    provenance = {
        "ocr_engine": best_run.engine_name if best_run else "manual",
        "ocr_confidence": best_run.mean_confidence if best_run else None,
        "ai_corrected": revision.source == "ai_correction",
        "page_count": document.page_count,
        "revision_no": revision.revision_no,
        "revision_source": revision.source,
    }

    quality_flags = list(result["quality_flags"])
    if best_run and (best_run.gibberish_score or 0) > 0.35:
        quality_flags.append("low_ocr_quality")
    if best_run and "suspected_summarization" in (best_run.warnings or []):
        quality_flags.append("ocr_may_be_incomplete")
    # A user-declared field that came back empty is a real signal, not a silent
    # omission — surface it instead of hiding it.
    for field in accepted:
        if field.get("user_required") and not result["custom_fields"].get(field["key"]):
            quality_flags.append(f"missing_required:{field['key']}")

    defaults = {
        "doc_type": metadata.doc_type.value,
        "title": metadata.title[:1024],
        "title_translit": (metadata.title_translit or "")[:1024],
        "primary_language": metadata.primary_language,
        "languages": metadata.languages,
        "summary_short": metadata.summary_short,
        "summary_long": metadata.summary_long,
        "keywords": metadata.keywords,
        "topics": metadata.topics,
        "document_date": extractor.parse_date(metadata.dates.document_date),
        "entities": metadata.entities.model_dump(),
        "dates": metadata.dates.model_dump(),
        "identifiers": metadata.identifiers.model_dump(),
        "provenance": provenance,
        "quality_flags": sorted(set(quality_flags)),
        "custom_fields": result["custom_fields"],
        "core": metadata.model_dump(mode="json"),
        "model_id": settings.LLM_MODEL,
        "prompt_version": extractor.PROMPT_VERSION,
        "validation_attempts": result["attempts"],
        "is_partial": result["is_partial"],
        "raw_llm_output": (result["raw"] or "")[:20000],
    }
    existing = MetadataRecord.objects.filter(document=document, human_edited=True).first()
    if existing is not None:
        # A person corrected this record by hand (enrichment/views.py sets the
        # flag). Re-running the model refreshes provenance and quality signals
        # but never overwrites what they wrote.
        defaults = {k: v for k, v in defaults.items() if k not in HUMAN_EDITABLE_FIELDS}
    record, _ = MetadataRecord.objects.update_or_create(document=document, defaults=defaults)

    # If the document had no title, adopt the extracted one for the library grid.
    if not document.title and record.title:
        document.title = record.title[:512]
        document.save(update_fields=["title", "updated_at"])

    try:
        fsm.transition_to(document, fsm.ENRICHED, payload={"is_partial": record.is_partial})
    except fsm.InvalidTransition:
        pass

    fsm.record_event(
        document,
        "enrichment_done",
        {
            "doc_type": record.doc_type,
            "is_partial": record.is_partial,
            "attempts": record.validation_attempts,
            "quality_flags": record.quality_flags,
        },
    )

    if auto_index:
        from rag.tasks import index_document

        index_document.delay(str(document.id))

    return {"doc_type": record.doc_type, "is_partial": record.is_partial}
