from __future__ import annotations

import logging
import time

from celery import shared_task
from django.conf import settings

from common import fsm
from common.ollama import OllamaError, get_client
from correction import prompts
from correction.consensus import locked_spans
from correction.guards import apply_guards
from correction.models import AICorrectionJob
from ocr.models import OCRPageResult

logger = logging.getLogger(__name__)


@shared_task(name="correction.tasks.run_ai_correction")
def run_ai_correction(job_id: str):
    """Propose corrections, page by page. Never applies them."""
    job = AICorrectionJob.objects.select_related("document", "base_revision").get(id=job_id)
    document = job.document
    revision = job.base_revision

    job.status = "running"
    job.model_id = settings.LLM_MODEL
    job.save(update_fields=["status", "model_id"])
    fsm.record_event(document, "ai_correction_started", {"job_id": str(job.id)})

    started = time.monotonic()
    client = get_client()
    pages = revision.pages
    page_rows = {p.page_number: p for p in document.pages.all()}

    # Where every engine agreed, correction is forbidden. Computed per page.
    consensus_by_page = _consensus_by_page(document)

    all_changes: list[dict] = []
    all_rejected: list[dict] = []
    reports: list[dict] = []
    wanted = set(job.pages or []) or None

    for index, page_text in enumerate(pages):
        page_number = index + 1
        if wanted and page_number not in wanted:
            continue
        if not page_text.strip():
            continue

        image_paths = []
        if job.multimodal and (row := page_rows.get(page_number)):
            rasters = row.raster_paths or {}
            # Prefer a higher-DPI raster; the model needs to resolve i'jam dots.
            path = next(
                (p for k, p in rasters.items() if k.startswith("classical")),
                next(iter(rasters.values()), None),
            )
            if path:
                image_paths = [path]

        try:
            parsed, raw = client.generate_json(
                settings.LLM_MODEL,
                prompts.USER.format(
                    page_number=page_number, page_count=len(pages), text=page_text
                ),
                prompts.SCHEMA,
                images=image_paths or None,
                system=prompts.SYSTEM,
                think=False,
            )
        except OllamaError as exc:
            logger.warning("correction failed on page %s: %s", page_number, exc)
            reports.append({"page": page_number, "error": str(exc)})
            continue

        if not parsed:
            reports.append({"page": page_number, "error": "unparseable output"})
            continue

        proposed = parsed.get("corrections", []) or []
        for change in proposed:
            change["page"] = page_number

        kept, rejected, report = apply_guards(
            page_text,
            proposed,
            locked=consensus_by_page.get(page_number, []),
            other_engine_texts=_other_engine_texts(document, page_number),
        )
        for change in kept:
            change["id"] = f"{page_number}-{change['span_start']}-{change['span_end']}"

        all_changes.extend(kept)
        all_rejected.extend(rejected)
        report["page"] = page_number
        report["page_is_illegible"] = parsed.get("page_is_illegible", False)
        reports.append(report)

        fsm.publish(
            document.id,
            "ai_correction_progress",
            {
                "job_id": str(job.id),
                "page": page_number,
                "of": len(pages),
                "accepted": len(kept),
                "rejected": len(rejected),
            },
        )

    job.changes = all_changes
    job.rejected = all_rejected
    job.guard_report = {
        "pages": reports,
        "total_proposed": sum(r.get("proposed", 0) for r in reports),
        "total_accepted": len(all_changes),
        "total_rejected": len(all_rejected),
        "rejections_by_rule": _merge_counts(reports),
    }
    job.status = "ready"
    job.duration_ms = int((time.monotonic() - started) * 1000)
    job.save()

    fsm.record_event(
        document,
        "ai_correction_ready",
        {
            "job_id": str(job.id),
            "accepted": len(all_changes),
            "rejected": len(all_rejected),
        },
    )
    return job.guard_report


def _consensus_by_page(document) -> dict[int, list[list[int]]]:
    """Spans where every successful engine agreed, per page."""
    latest = document.ocr_batches.order_by("-created_at").first()
    if not latest:
        return {}
    runs = list(latest.runs.filter(status="succeeded"))
    if len(runs) < 2:
        return {}

    by_page: dict[int, list[str]] = {}
    for result in OCRPageResult.objects.filter(run__in=runs):
        by_page.setdefault(result.page_number, []).append(result.text)

    return {page: locked_spans(texts)[0] for page, texts in by_page.items()}


def _other_engine_texts(document, page_number: int) -> list[str]:
    latest = document.ocr_batches.order_by("-created_at").first()
    if not latest:
        return []
    return [
        r.text
        for r in OCRPageResult.objects.filter(
            run__batch=latest, run__status="succeeded", page_number=page_number
        )
    ]


def _merge_counts(reports: list[dict]) -> dict:
    merged: dict[str, int] = {}
    for report in reports:
        for rule, count in (report.get("rejections_by_rule") or {}).items():
            merged[rule] = merged.get(rule, 0) + count
    return merged
