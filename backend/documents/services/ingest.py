"""Create documents from files and drive them through the pipeline.

`create_document` is what the upload endpoint does; `process_document` is the
sequence a person clicks through in the UI (OCR, pick the best run, finalize,
enrich, index), used by the dev-only `ingest_folder` command.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import time
from typing import Callable, Iterable

from django.conf import settings
from django.db import transaction

from common import fsm
from documents.models import Document, TextRevision

PREPROCESS_PROFILES = ["neural"]
# Statuses after which the document will not move on its own.
STUCK = {fsm.FAILED, fsm.OCR_FAILED}


class IngestError(RuntimeError):
    """A stage of the pipeline did not get the document where it had to be."""


def create_document(
    owner,
    upload,
    *,
    title: str = "",
    metadata_mode: str = "auto",
    required_fields: list | None = None,
) -> Document:
    """Store a validated upload (see UploadSerializer) as a new document.

    The file is written under MEDIA_ROOT with an extension taken from the
    sniffed type, hashed on the way, and preprocessing is queued once the
    surrounding transaction (if any) commits.
    """
    document = Document.objects.create(
        owner=owner,
        title=title,
        original_filename=upload.name[:512],
        mime_type=upload.sniffed_mime,
        size_bytes=upload.size,
        metadata_mode=metadata_mode,
        required_fields=required_fields or [],
        storage_path="",
    )

    target_dir = os.path.join(settings.MEDIA_ROOT, "docs", str(document.id))
    os.makedirs(target_dir, exist_ok=True)
    # From the sniffed type, not the client's filename.
    extension = mimetypes.guess_extension(upload.sniffed_mime) or ".bin"
    target_path = os.path.join(target_dir, f"original{extension}")

    digest = hashlib.sha256()
    with open(target_path, "wb") as fh:
        for chunk in upload.chunks():
            digest.update(chunk)
            fh.write(chunk)

    document.storage_path = target_path
    document.sha256 = digest.hexdigest()
    document.save(update_fields=["storage_path", "sha256", "updated_at"])

    # Preprocessing (page count, digital-text detection) runs immediately so
    # the UI can show page thumbnails and recommend engines right away.
    from ocr.tasks import preprocess_document

    transaction.on_commit(lambda: preprocess_document.delay(str(document.id), PREPROCESS_PROFILES))
    return document


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_status(document: Document, targets: Iterable[str], *, timeout: float, poll: float = 2.0) -> str:
    """Poll until the document reaches one of `targets`. Raises IngestError on
    a failed status or when `timeout` seconds pass."""
    targets = set(targets)
    started = time.monotonic()
    while True:
        document.refresh_from_db(fields=["status", "status_detail"])
        if document.status in targets:
            return document.status
        if document.status in STUCK:
            detail = f": {document.status_detail}" if document.status_detail else ""
            raise IngestError(f"document failed ({document.status}{detail})")
        if time.monotonic() - started > timeout:
            raise IngestError(f"timed out after {timeout:.0f}s waiting for {sorted(targets)} (still {document.status})")
        time.sleep(poll)


def select_best_and_finalize(document: Document, batch) -> TextRevision:
    """Adopt the best succeeded run of `batch` as revision 1 and finalize it,
    exactly as the compare grid + "use this text" + finalize do in the UI."""
    from documents.views import _next_revision
    from ocr.ranking import rank_key

    runs = list(batch.runs.filter(status="succeeded"))
    if not runs:
        raise IngestError("no OCR run succeeded")
    best = max(runs, key=rank_key)

    revision = _next_revision(
        document, text=best.text, source="ocr_pick", origin_run=best, note=f"selected {best.engine_name}",
    )
    with transaction.atomic():
        document.revisions.update(is_final=False)
        revision.is_final = True
        revision.save(update_fields=["is_final"])
        document.current_revision = revision
        document.save(update_fields=["current_revision", "updated_at"])
        fsm.transition_to(document, fsm.TEXT_FINALIZED, payload={"revision_no": revision.revision_no})
    return revision


def process_document(
    document: Document,
    *,
    engines: list[str],
    languages: list[str],
    timeout: float,
    poll: float = 2.0,
    on_stage: Callable[[str], None] | None = None,
) -> None:
    """Take a freshly created document all the way to READY."""
    from enrichment.tasks import enrich_document
    from ocr.tasks import start_ocr_batch

    def stage(name: str) -> None:
        if on_stage:
            on_stage(name)

    wait_for_status(document, {fsm.PREPROCESSED}, timeout=timeout, poll=poll)
    stage("preprocessed")

    batch = start_ocr_batch(document, engines=engines, languages=languages, options={})
    status = wait_for_status(document, {fsm.OCR_DONE, fsm.OCR_PARTIAL}, timeout=timeout, poll=poll)
    if status == fsm.OCR_PARTIAL:
        # The state machine has no path from a partial batch to a finalized
        # text (the UI refuses too); the document is left for a person to
        # re-run OCR with the engines that worked.
        batch.refresh_from_db()
        failed = sorted(batch.runs.exclude(status="succeeded").values_list("engine_name", flat=True))
        raise IngestError(f"OCR finished partially (failed: {', '.join(failed)}); left at ocr_partial for review")
    stage(f"ocr ({', '.join(engines)})")

    batch.refresh_from_db()
    select_best_and_finalize(document, batch)
    stage("finalized")

    enrich_document.delay(str(document.id), auto_index=True)
    wait_for_status(document, {fsm.READY}, timeout=timeout, poll=poll)
    stage("ready")
