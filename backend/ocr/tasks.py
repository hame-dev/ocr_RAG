"""Celery orchestration for OCR.

THE critical invariant in this module: `run_ocr_engine` NEVER RAISES.

Engines run inside a chord. If a chord member raises, the callback never fires
and the batch hangs in `running` forever with no way to recover — that is the
single most likely way this pipeline breaks. So every failure is caught, written
to the run row, published, and returned as a summary dict.
"""
from __future__ import annotations

import logging
import time
import traceback

from celery import chain, chord, group, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from common import fsm
from common.arabic import arabic_char_ratio, gibberish_score
from documents.models import Document, DocumentPage
from documents.services import preprocess
from ocr.engines import registry
from ocr.engines.base import (
    EngineNotApplicable,
    OCRJobInput,
    PageImage,
    TransientEngineError,
)
from ocr.models import EngineHealthSnapshot, OCRBatch, OCRPageResult, OCRRun

logger = logging.getLogger(__name__)


@shared_task(name="ocr.tasks.probe_engine_health")
def probe_engine_health():
    """Beat task: keep the engine catalog warm so the UI never waits on a probe."""
    entries = registry.probe_all(force=True)
    for entry in entries:
        EngineHealthSnapshot.objects.create(
            engine_name=entry["name"],
            available=entry["available"],
            version=entry.get("version") or "",
            detail=(entry.get("detail") or "")[:512],
            latency_ms=entry.get("latency_ms"),
        )
    return {"engines": len(entries), "available": sum(e["available"] for e in entries)}


@shared_task(name="documents.tasks.preprocess_document")
def preprocess_document(document_id: str, profiles: list[str] | None = None):
    """Analyze the document and render every raster profile the batch needs."""
    document = Document.objects.get(id=document_id)

    if document.status in (fsm.UPLOADED, fsm.FAILED):
        fsm.transition_to(document, fsm.PREPROCESSING)

    try:
        preprocess.analyze(document)
        for profile_name in profiles or ["neural"]:
            preprocess.rasterize(document, profile_name)
    except Exception as exc:
        logger.exception("preprocessing failed for %s", document_id)
        document.error_code = "preprocess_failed"
        document.error_message = str(exc)
        document.save(update_fields=["error_code", "error_message", "updated_at"])
        fsm.transition_to(document, fsm.FAILED, detail=str(exc)[:500])
        raise

    if document.status == fsm.PREPROCESSING:
        fsm.transition_to(document, fsm.PREPROCESSED)
    return {"document_id": document_id, "pages": document.page_count}


def _build_job(document: Document, engine, batch: OCRBatch) -> OCRJobInput:
    from ocr.preprocess.profiles import profile as get_profile
    from ocr.preprocess.profiles import raster_key

    profile_name = engine.preferred_profile
    prof = get_profile(profile_name)
    dpi = prof["dpi"]
    key = raster_key(profile_name, dpi)

    pages = list(DocumentPage.objects.filter(document=document).order_by("page_number"))
    page_images = [
        PageImage(
            page_number=p.page_number,
            path=(p.raster_paths or {}).get(key, ""),
            dpi=dpi,
            width_px=p.width_px,
            height_px=p.height_px,
        )
        for p in pages
        if (p.raster_paths or {}).get(key)
    ]

    return OCRJobInput(
        document_id=str(document.id),
        source_path=document.storage_path,
        page_images=page_images,
        languages=batch.languages or ["ara", "eng"],
        options=batch.options or {},
        deadline_s=engine.default_timeout_s,
        native_page_texts=[p.native_text for p in pages] if document.is_digital_pdf else [],
    )


@shared_task(
    bind=True,
    name="ocr.tasks.run_ocr_engine",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=2,
)
def run_ocr_engine(self, batch_id: str, engine_name: str):
    """Run one engine. Catches everything — see the module docstring."""
    started = time.monotonic()
    batch = OCRBatch.objects.select_related("document").get(id=batch_id)
    document = batch.document

    run, _ = OCRRun.objects.get_or_create(
        batch=batch,
        engine_name=engine_name,
        defaults={"document": document, "status": "queued"},
    )

    try:
        engine = registry.get(engine_name)
    except KeyError as exc:
        return _fail(run, document, "unknown_engine", str(exc))

    run.status = "running"
    run.preprocess_profile = engine.preferred_profile
    run.save(update_fields=["status", "preprocess_profile"])
    fsm.record_event(
        document,
        "ocr_run_started",
        {"batch_id": str(batch.id), "engine": engine_name, "run_id": str(run.id)},
    )

    def on_page(page):
        # Drives the per-engine progress bar. Slow engines would otherwise leave
        # the user staring at a spinner for minutes.
        fsm.publish(
            document.id,
            "ocr_page_done",
            {
                "batch_id": str(batch.id),
                "engine": engine_name,
                "run_id": str(run.id),
                "page": page.page_number,
                "of": document.page_count or len(job.page_images),
                "chars": len(page.text),
            },
        )

    try:
        job = _build_job(document, engine, batch)

        if engine.needs_raster and not job.page_images:
            return _fail(
                run, document, "no_rasters",
                "no rendered pages available for this engine's profile",
            )

        result = engine.run(job, on_page=on_page)

    except EngineNotApplicable as exc:
        # Not a failure: this engine simply does not apply to this input.
        run.status = "skipped"
        run.error_code = "not_applicable"
        run.error_message = str(exc)
        run.finished_at = timezone.now()
        run.save()
        _publish_finished(document, batch, run)
        return {"engine": engine_name, "status": "skipped", "detail": str(exc)}

    except TransientEngineError as exc:
        # A crashed sidecar or a dropped connection is worth retrying; a missing
        # language pack is not, which is why only this exception type retries.
        if self.request.retries < self.max_retries:
            logger.warning(
                "transient failure in %s (retry %s/%s): %s",
                engine_name, self.request.retries + 1, self.max_retries, exc,
            )
            run.status = "queued"
            run.save(update_fields=["status"])
            # Retry propagates deliberately: Celery interprets it as "re-queue",
            # not as a task failure, so the chord stays intact. It must NOT be
            # caught by the broad handler below.
            raise self.retry(exc=exc, countdown=5 * (self.request.retries + 1))
        return _fail(run, document, "transient_failure", str(exc))

    except SoftTimeLimitExceeded as exc:
        return _fail(run, document, "timeout", str(exc), status="timeout")

    except BaseException as exc:  # noqa: BLE001 - deliberate: nothing escapes
        logger.exception("engine %s failed on document %s", engine_name, document.id)
        return _fail(
            run, document, type(exc).__name__, f"{exc}\n{traceback.format_exc(limit=3)}"
        )

    # ---- success ------------------------------------------------------------
    text = result.text
    run.status = "succeeded"
    run.engine_version = result.engine_version or ""
    run.model_id = result.model_id or ""
    run.text = text
    run.mean_confidence = result.mean_confidence
    run.duration_ms = result.duration_ms or int((time.monotonic() - started) * 1000)
    run.char_count = len(text)
    run.word_count = len(text.split())
    run.arabic_char_ratio = arabic_char_ratio(text)
    run.gibberish_score = gibberish_score(text)
    run.warnings = list(result.warnings)
    run.dpi = job.page_images[0].dpi if job.page_images else None
    run.finished_at = timezone.now()

    # A VLM that describes or summarizes instead of transcribing produces short,
    # fluent, wrong output. Flag it so it is visibly de-ranked rather than
    # silently trusted. Compared against sibling runs on the same batch.
    _flag_suspected_summarization(run, batch)

    run.save()

    OCRPageResult.objects.filter(run=run).delete()
    OCRPageResult.objects.bulk_create(
        [
            OCRPageResult(
                run=run,
                page_number=p.page_number,
                text=p.text,
                confidence=p.confidence,
                lines=[line.to_json() for line in p.lines][:2000],
                duration_ms=p.duration_ms,
                warnings=p.warnings,
                raw=p.raw or {},
            )
            for p in result.pages
        ]
    )

    _publish_finished(document, batch, run)
    return {
        "engine": engine_name,
        "status": "succeeded",
        "chars": run.char_count,
        "duration_ms": run.duration_ms,
    }


def _flag_suspected_summarization(run: OCRRun, batch: OCRBatch) -> None:
    siblings = [
        r.char_count
        for r in batch.runs.filter(status="succeeded").exclude(id=run.id)
        if r.char_count > 0
    ]
    if not siblings:
        return
    siblings.sort()
    median = siblings[len(siblings) // 2]
    if median and run.char_count < 0.4 * median:
        run.warnings = list(run.warnings) + ["suspected_summarization"]
        run.gibberish_score = min(1.0, (run.gibberish_score or 0.0) + 0.4)


def _fail(run: OCRRun, document, code: str, message: str, status: str = "failed"):
    run.status = status
    run.error_code = code[:64]
    run.error_message = message[:4000]
    run.finished_at = timezone.now()
    run.save()
    fsm.publish(
        document.id,
        "ocr_run_finished",
        {
            "batch_id": str(run.batch_id),
            "engine": run.engine_name,
            "run_id": str(run.id),
            "status": status,
            "error_code": code,
            "error_message": message[:300],
        },
    )
    return {"engine": run.engine_name, "status": status, "error": code}


def _publish_finished(document, batch: OCRBatch, run: OCRRun) -> None:
    fsm.publish(
        document.id,
        "ocr_run_finished",
        {
            "batch_id": str(batch.id),
            "engine": run.engine_name,
            "run_id": str(run.id),
            "status": run.status,
            "duration_ms": run.duration_ms,
            "mean_confidence": run.mean_confidence,
            "char_count": run.char_count,
            "gibberish_score": run.gibberish_score,
            "warnings": run.warnings,
            "preview": run.preview,
        },
    )
    # Move to ocr_partial as soon as the FIRST engine succeeds, so the user can
    # start reading while the rest are still running.
    status = batch.derive_status()
    if status == "partial" and document.status == fsm.OCR_RUNNING:
        fsm.transition_to(document, fsm.OCR_PARTIAL)
    batch.status = status
    batch.save(update_fields=["status"])
    fsm.publish(document.id, "batch_status", {"batch_id": str(batch.id), "status": status})


@shared_task(name="ocr.tasks.finalize_ocr_batch")
def finalize_ocr_batch(results, batch_id: str):
    batch = OCRBatch.objects.select_related("document").get(id=batch_id)
    document = batch.document

    status = batch.derive_status()
    batch.status = status
    batch.finished_at = timezone.now()
    batch.save(update_fields=["status", "finished_at"])

    if status == "done":
        target = fsm.OCR_DONE
    elif status == "partial":
        target = fsm.OCR_PARTIAL
    else:
        target = fsm.OCR_FAILED

    try:
        fsm.transition_to(document, target, payload={"batch_id": str(batch.id)})
    except fsm.InvalidTransition:
        logger.warning("could not move %s to %s from %s", document.id, target, document.status)

    fsm.publish(
        document.id,
        "batch_status",
        {"batch_id": str(batch.id), "status": status, "final": True},
    )
    return {"batch_id": batch_id, "status": status, "runs": len(results or [])}


def start_ocr_batch(document: Document, engines: list[str], languages: list[str],
                    options: dict | None = None) -> OCRBatch:
    """Kick off a multi-engine run: preprocess once, then fan out."""
    batch = OCRBatch.objects.create(
        document=document,
        requested_engines=engines,
        languages=languages or ["ara", "eng"],
        options=options or {},
        status="queued",
        started_at=timezone.now(),
    )

    for engine_name in engines:
        OCRRun.objects.get_or_create(
            batch=batch,
            engine_name=engine_name,
            defaults={"document": document, "status": "queued"},
        )

    # Render each distinct profile once and share it across engines, rather than
    # once per engine.
    profiles = sorted({registry.get(name).preferred_profile for name in engines})

    if document.status in (fsm.PREPROCESSED, fsm.OCR_DONE, fsm.OCR_PARTIAL,
                           fsm.OCR_FAILED, fsm.READY, fsm.TEXT_FINALIZED):
        fsm.transition_to(document, fsm.OCR_RUNNING, payload={"batch_id": str(batch.id)})

    workflow = chain(
        preprocess_document.si(str(document.id), profiles),
        chord(group(_engine_signatures(str(batch.id), engines)),
              finalize_ocr_batch.s(str(batch.id))),
    )
    async_result = workflow.apply_async()
    batch.celery_group_id = str(async_result.id)
    batch.status = "running"
    batch.save(update_fields=["celery_group_id", "status"])
    return batch


def _engine_signatures(batch_id: str, engines: list[str]) -> list:
    """Build immutable, engine-routed chord headers (also easy to unit-test)."""
    return [
        # Immutable: the preceding preprocess task returns a summary dict,
        # which a normal signature would prepend as an unwanted third argument.
        run_ocr_engine.si(batch_id, name).set(queue=registry.get(name).queue)
        for name in engines
    ]
