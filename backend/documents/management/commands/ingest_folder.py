from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from common import fsm
from documents.models import Document, TextRevision
from ocr.tasks import start_ocr_batch
from ocr.models import OCRBatch, OCRRun
from rag.models import IndexRun


class Command(BaseCommand):
    help = (
        "Recursively ingest PDFs using Embedded PDF text "
        "and the existing RAG indexing pipeline."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "folder",
            type=str,
            help="Folder containing processed PDFs",
        )
        parser.add_argument(
            "--user",
            default="NCST_system",
            help="Document owner username",
        )
        parser.add_argument(
            "--no-wait",
            action="store_true",
            help="Queue documents and exit immediately",
        )

    def handle(self, *args, **options):
        source = Path(options["folder"]).resolve()
        username = options["user"]
        no_wait = options["no_wait"]

        if not source.exists():
            raise RuntimeError(f"Folder does not exist: {source}")

        if not source.is_dir():
            raise RuntimeError(f"Not a directory: {source}")

        from django.contrib.auth import get_user_model

        User = get_user_model()

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise RuntimeError(
                f"User {username!r} does not exist.\n"
                f"Create it first with: make createuser"
            )

        pdfs = sorted(
            p
            for p in source.rglob("*")
            if p.is_file() and p.suffix.lower() == ".pdf"
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Found {len(pdfs):,} PDF files."
            )
        )

        if not pdfs:
            return

        self.stdout.write(
            "Using engine: native_pdf (Embedded PDF text)"
        )
        self.stdout.write(f"Owner: {username}\n")

        # -------------------------------------------------
        # Prepare / resume documents
        # -------------------------------------------------

        new_documents = []
        resume_documents = []

        ready = 0
        repaired = 0
        repair_failed = 0

        self.stdout.write("Checking existing documents...\n")

        for index, pdf in enumerate(pdfs, 1):
            try:
                digest = self.sha256(pdf)

                existing = (
                    Document.objects
                    .filter(sha256=digest)
                    .first()
                )

                if existing:
                    # Already completely indexed.
                    if existing.status == fsm.READY:
                        ready += 1
                        continue

                    # -------------------------------------------------
                    # Important:
                    # If RAG indexing already succeeded, repair the FSM
                    # state instead of running OCR/RAG again.
                    # -------------------------------------------------

                    successful_index = (
                        IndexRun.objects
                        .filter(
                            document=existing,
                            status="succeeded",
                        )
                        .order_by("-created_at")
                        .first()
                    )

                    if successful_index:
                        try:
                            self.repair_indexed_document(existing)
                            repaired += 1

                            self.stdout.write(
                                f"\nREPAIRED: "
                                f"{existing.original_filename}"
                            )

                            continue

                        except Exception as exc:
                            repair_failed += 1

                            self.stderr.write(
                                self.style.ERROR(
                                    f"\nREPAIR FAILED: "
                                    f"{existing.original_filename}\n"
                                    f"{exc}"
                                )
                            )

                    resume_documents.append(
                        (existing, pdf)
                    )

                    continue

                # -------------------------------------------------
                # New document
                # -------------------------------------------------

                document = Document.objects.create(
                    owner=user,
                    title=pdf.stem[:512],
                    original_filename=pdf.name[:512],
                    mime_type="application/pdf",
                    size_bytes=pdf.stat().st_size,
                    sha256=digest,
                    storage_path="",
                    status=fsm.UPLOADED,
                    metadata_mode="auto",
                    required_fields=[],
                )

                target_dir = (
                    Path(settings.MEDIA_ROOT)
                    / "docs"
                    / str(document.id)
                )

                target_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                target = target_dir / "original.pdf"

                shutil.copy2(pdf, target)

                document.storage_path = str(target)

                document.save(
                    update_fields=[
                        "storage_path",
                        "updated_at",
                    ]
                )

                new_documents.append(
                    (document, pdf)
                )

            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(
                        f"FAILED: {pdf}\n{exc}"
                    )
                )

            if index % 100 == 0:
                self.stdout.write(
                    f"  checked {index:,}/{len(pdfs):,}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nNew documents: {len(new_documents):,}"
            )
        )

        self.stdout.write(
            f"Documents to resume: {len(resume_documents):,}"
        )

        self.stdout.write(
            f"Already READY: {ready:,}"
        )

        self.stdout.write(
            f"Already indexed + repaired: {repaired:,}"
        )

        self.stdout.write(
            f"Repair failures: {repair_failed:,}\n"
        )

        # -------------------------------------------------
        # Queue OCR
        # -------------------------------------------------

        work = new_documents + resume_documents

        batches = []

        self.stdout.write(
            f"Starting Embedded PDF text processing for "
            f"{len(work):,} documents...\n"
        )

        for document, source_pdf in work:
            try:
                # If this document already has a running OCR batch,
                # don't create another one.
                existing_batch = (
                    OCRBatch.objects
                    .filter(
                        document=document,
                        status="running",
                    )
                    .order_by("-created_at")
                    .first()
                )

                if existing_batch:
                    batches.append(
                        (document, existing_batch)
                    )
                    continue

                # -------------------------------------------------
                # If OCR already succeeded and a revision exists,
                # don't run OCR again.
                # -------------------------------------------------

                if (
                    document.current_revision
                    and document.current_revision.text.strip()
                ):
                    try:
                        if document.status == fsm.OCR_DONE:
                            fsm.transition_to(
                                document,
                                fsm.TEXT_FINALIZED,
                                actor="system",
                                payload={
                                    "source": "ingestion_resume",
                                },
                            )

                        if document.status == fsm.TEXT_FINALIZED:
                            fsm.transition_to(
                                document,
                                fsm.ENRICHING,
                                actor="system",
                                payload={
                                    "source": "ingestion_resume",
                                },
                            )

                        if document.status == fsm.ENRICHING:
                            fsm.transition_to(
                                document,
                                fsm.ENRICHED,
                                actor="system",
                                payload={
                                    "source": "ingestion_resume",
                                },
                            )

                        if document.status == fsm.ENRICHED:
                            from rag.tasks import index_document

                            index_document.delay(
                                str(document.id)
                            )

                            batches.append(
                                (document, None)
                            )

                            continue

                    except Exception:
                        # If resume repair cannot safely continue,
                        # fall back to the normal OCR pipeline.
                        pass

                # -------------------------------------------------
                # Reset failed documents so the normal pipeline
                # can process them again.
                # -------------------------------------------------

                if document.status == fsm.FAILED:
                    document.status = fsm.UPLOADED
                    document.error_code = ""
                    document.error_message = ""

                    document.save(
                        update_fields=[
                            "status",
                            "error_code",
                            "error_message",
                            "updated_at",
                        ]
                    )

                batch = start_ocr_batch(
                    document,
                    engines=["native_pdf"],
                    languages=["ara", "eng"],
                    options={
                        "ingestion": True,
                    },
                )

                batches.append(
                    (document, batch)
                )

            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(
                        f"FAILED TO QUEUE: "
                        f"{source_pdf}\n{exc}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Queued/resumed: {len(batches):,} PDFs."
            )
        )

        if no_wait:
            self.stdout.write(
                "\nDone. Celery workers are processing them."
            )
            return

        # -------------------------------------------------
        # Monitor OCR + RAG
        # -------------------------------------------------

        self.stdout.write(
            "\nMonitoring OCR + RAG indexing..."
        )

        pending = {
            str(document.id): (document, batch)
            for document, batch in batches
            if batch is not None
        }

        ocr_done = 0
        indexed = 0
        failed = 0
        skipped = 0

        # Documents for which OCR has already been converted
        # into a revision and index task has been queued.
        indexing = {}

        while pending or indexing:

            # ---------------------------------------------
            # OCR stage
            # ---------------------------------------------

            for document_id in list(pending.keys()):
                document, batch = pending[document_id]

                run = (
                    OCRRun.objects
                    .filter(
                        batch=batch,
                        engine_name="native_pdf",
                    )
                    .first()
                )

                if not run:
                    continue

                if run.status not in {
                    "succeeded",
                    "failed",
                    "skipped",
                }:
                    continue

                pending.pop(document_id)

                if run.status == "succeeded":
                    try:
                        self.create_revision(
                            document,
                            run,
                        )

                        # -------------------------------------------------
                        # IMPORTANT FIX
                        #
                        # index_document expects the document to have
                        # reached ENRICHED before it can move into
                        # INDEXING.
                        # -------------------------------------------------

                        if document.status == fsm.TEXT_FINALIZED:
                            fsm.transition_to(
                                document,
                                fsm.ENRICHING,
                                actor="system",
                                payload={
                                    "source": "native_pdf",
                                },
                            )

                        if document.status == fsm.ENRICHING:
                            fsm.transition_to(
                                document,
                                fsm.ENRICHED,
                                actor="system",
                                payload={
                                    "source": "native_pdf",
                                },
                            )

                        from rag.tasks import index_document

                        index_document.delay(
                            str(document.id)
                        )

                        indexing[document_id] = document
                        ocr_done += 1

                    except Exception as exc:
                        failed += 1

                        self.stderr.write(
                            self.style.ERROR(
                                f"\nINDEX QUEUE FAILED: "
                                f"{document.original_filename}\n"
                                f"{exc}"
                            )
                        )

                elif run.status == "skipped":
                    skipped += 1

                else:
                    failed += 1

                    self.stderr.write(
                        self.style.ERROR(
                            f"\nOCR FAILED: "
                            f"{document.original_filename}\n"
                            f"{run.error_message}"
                        )
                    )

            # ---------------------------------------------
            # RAG stage
            # ---------------------------------------------

            for document_id in list(indexing.keys()):
                document = indexing[document_id]

                document.refresh_from_db()

                if document.status == fsm.READY:
                    indexed += 1
                    indexing.pop(document_id)

                elif document.status == fsm.FAILED:
                    failed += 1
                    indexing.pop(document_id)

            total = len(batches)

            self.stdout.write(
                f"\rOCR: {ocr_done:,}/{total:,} "
                f"| RAG indexed: {indexed:,}/{total:,} "
                f"| OCR/RAG failed: {failed:,} "
                f"| skipped: {skipped:,} "
                f"| pending OCR: {len(pending):,} "
                f"| indexing: {len(indexing):,}",
                ending="",
            )

            if pending or indexing:
                time.sleep(2)

        self.stdout.write("\n")

        self.stdout.write(
            self.style.SUCCESS(
                "\nIngestion complete."
            )
        )

        self.stdout.write(
            f"RAG indexed successfully: {indexed:,}"
        )

        self.stdout.write(
            f"\nAlready READY: {ready:,}"
        )

        self.stdout.write(
            f"\nAlready repaired: {repaired:,}"
        )

        self.stdout.write(
            f"\nFailed: {failed:,}"
        )

        self.stdout.write(
            f"\nSkipped/not applicable: {skipped:,}"
        )

    # =====================================================
    # REPAIR ALREADY-INDEXED DOCUMENT
    # =====================================================

    @staticmethod
    def repair_indexed_document(document):
        """
        Bring a document with a successful IndexRun through the
        normal FSM lifecycle without rerunning OCR or embeddings.

        Example:

            ocr_done
                -> text_finalized
                -> enriching
                -> enriched
                -> indexing
                -> indexed
                -> ready
        """

        if document.status == fsm.READY:
            return

        if document.status == fsm.OCR_DONE:
            fsm.transition_to(
                document,
                fsm.TEXT_FINALIZED,
                actor="system",
                payload={
                    "repair": "ingestion",
                },
            )

        if document.status == fsm.TEXT_FINALIZED:
            fsm.transition_to(
                document,
                fsm.ENRICHING,
                actor="system",
                payload={
                    "repair": "ingestion",
                },
            )

        if document.status == fsm.ENRICHING:
            fsm.transition_to(
                document,
                fsm.ENRICHED,
                actor="system",
                payload={
                    "repair": "ingestion",
                },
            )

        if document.status == fsm.ENRICHED:
            fsm.transition_to(
                document,
                fsm.INDEXING,
                actor="system",
                payload={
                    "repair": "ingestion",
                },
            )

        if document.status == fsm.INDEXING:
            fsm.transition_to(
                document,
                fsm.INDEXED,
                actor="system",
                payload={
                    "repair": "ingestion",
                },
            )

        if document.status == fsm.INDEXED:
            fsm.transition_to(
                document,
                fsm.READY,
                actor="system",
                payload={
                    "repair": "ingestion",
                },
            )

    # =====================================================
    # SHA256
    # =====================================================

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()

        with path.open("rb") as fh:
            for chunk in iter(
                lambda: fh.read(1024 * 1024),
                b"",
            ):
                digest.update(chunk)

        return digest.hexdigest()

    # =====================================================
    # CREATE REVISION
    # =====================================================

    def create_revision(self, document, run):
        # Don't create duplicate revisions if this command
        # is restarted after OCR already completed.

        existing = (
            TextRevision.objects
            .filter(
                document=document,
                origin_run=run,
            )
            .order_by("-revision_no")
            .first()
        )

        if existing:
            document.current_revision = existing

            document.save(
                update_fields=[
                    "current_revision",
                    "updated_at",
                ]
            )

            # Make sure an existing revision also enters
            # the correct FSM lifecycle.
            if document.status == fsm.OCR_DONE:
                fsm.transition_to(
                    document,
                    fsm.TEXT_FINALIZED,
                    actor="system",
                    payload={
                        "revision_no": existing.revision_no,
                        "source": "native_pdf",
                    },
                )

            return existing

        with transaction.atomic():
            last = (
                TextRevision.objects
                .select_for_update()
                .filter(document=document)
                .order_by("-revision_no")
                .first()
            )

            revision = TextRevision.objects.create(
                document=document,
                revision_no=(
                    last.revision_no + 1
                    if last
                    else 1
                ),
                source="ocr_pick",
                parent=None,
                origin_run=run,
                text=run.text,
                note="selected native_pdf during folder ingestion",
                created_by="system",
            )

            document.current_revision = revision

            document.save(
                update_fields=[
                    "current_revision",
                    "updated_at",
                ]
            )

        revision.is_final = True

        revision.save(
            update_fields=["is_final"]
        )

        if document.status == fsm.OCR_DONE:
            fsm.transition_to(
                document,
                fsm.TEXT_FINALIZED,
                actor="system",
                payload={
                    "revision_no": revision.revision_no,
                    "source": "native_pdf",
                },
            )

        return revision