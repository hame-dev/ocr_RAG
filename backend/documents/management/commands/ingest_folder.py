"""DEV ONLY: run the whole pipeline on every PDF/image in a folder.

    python manage.py ingest_folder /ingest --user alice [--recursive] [--no-wait]

Each file becomes a document owned by --user and goes through what a person
clicks in the UI: preprocess, OCR with --engines, pick the best run, finalize,
enrich, index. Files whose content was already ingested by that user are
skipped, so re-running on the same folder is safe. With --no-wait the files
are only uploaded (preprocessing queued) and the rest is left to the UI.

From the host the folder has to be mounted into the container:
    make ingest D=~/scans U=alice ARGS="--recursive"
"""
from __future__ import annotations

import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from documents.models import Document
from documents.serializers import UploadSerializer
from documents.services.ingest import IngestError, create_document, file_sha256, process_document


class Command(BaseCommand):
    help = "Dev only: ingest every PDF/image in a folder and run the pipeline to READY."

    def add_arguments(self, parser):
        parser.add_argument("folder")
        parser.add_argument("--user", required=True, help="Owner of the new documents.")
        parser.add_argument("--engines", default=None, help=f"Comma-separated; default {settings.DEFAULT_OCR_ENGINE}.")
        parser.add_argument("--languages", default="ara,eng", help="Comma-separated OCR languages.")
        parser.add_argument("--recursive", action="store_true", help="Include subfolders.")
        parser.add_argument("--no-wait", action="store_true", help="Upload and preprocess only.")
        parser.add_argument("--timeout", type=float, default=1800, help="Seconds per stage per document.")
        parser.add_argument("--poll", type=float, default=3.0, help="Seconds between status checks.")
        parser.add_argument("--force", action="store_true", help="Skip the engine health check.")

    def handle(self, *args, **options):
        folder = Path(options["folder"])
        if not folder.is_dir():
            raise CommandError(f"not a directory: {folder}")
        owner = get_user_model().objects.filter(username=options["user"]).first()
        if owner is None:
            raise CommandError(f"no such user: {options['user']}")

        engines = [e.strip() for e in (options["engines"] or settings.DEFAULT_OCR_ENGINE).split(",") if e.strip()]
        languages = [l.strip() for l in options["languages"].split(",") if l.strip()]
        if not options["no_wait"] and not options["force"]:
            self._check_engines(engines)

        paths = sorted(p for p in (folder.rglob("*") if options["recursive"] else folder.iterdir())
                       if p.is_file() and not p.name.startswith("."))
        counts = {"ingested": 0, "skipped": 0, "failed": 0}
        for path in paths:
            label = str(path.relative_to(folder))
            try:
                outcome = self._ingest(path, label, owner, engines, languages, options)
            except IngestError as exc:
                counts["failed"] += 1
                self.stdout.write(self.style.ERROR(f"{label:<40} failed: {exc}"))
                continue
            counts[outcome] += 1

        summary = ", ".join(f"{v} {k}" for k, v in counts.items())
        self.stdout.write(self.style.SUCCESS(summary) if not counts["failed"] else self.style.WARNING(summary))
        if counts["failed"]:
            raise CommandError(f"{counts['failed']} file(s) failed")

    def _check_engines(self, engines: list[str]) -> None:
        from ocr.engines import registry

        unknown = [e for e in engines if e not in set(registry.names())]
        if unknown:
            raise CommandError(f"unknown engines: {', '.join(unknown)} (known: {', '.join(sorted(registry.names()))})")
        healthy = {e["name"] for e in registry.probe_all() if e["available"]}
        unhealthy = [e for e in engines if e not in healthy]
        if unhealthy:
            raise CommandError(f"engines not available: {', '.join(unhealthy)} (use --force to try anyway)")

    def _ingest(self, path: Path, label: str, owner, engines, languages, options) -> str:
        digest = file_sha256(str(path))
        existing = Document.objects.filter(owner=owner, sha256=digest).first()
        if existing is not None:
            self.stdout.write(f"{label:<40} skipped: already ingested ({existing.display_title})")
            return "skipped"

        with open(path, "rb") as fh:
            serializer = UploadSerializer(data={"file": File(fh, name=path.name)})
            if not serializer.is_valid():
                reasons = "; ".join(str(e) for errors in serializer.errors.values() for e in errors)
                self.stdout.write(f"{label:<40} skipped: {reasons}")
                return "skipped"
            document = create_document(owner, serializer.validated_data["file"])

        if options["no_wait"]:
            self.stdout.write(f"{label:<40} uploaded (preprocessing queued) {document.id}")
            return "ingested"

        started = time.monotonic()
        stages: list[str] = []

        def on_stage(name: str) -> None:
            stages.append(name)
            self.stdout.write(f"{label:<40} {' → '.join(['uploaded', *stages])}")

        process_document(document, engines=engines, languages=languages,
                         timeout=options["timeout"], poll=options["poll"], on_stage=on_stage)
        self.stdout.write(self.style.SUCCESS(f"{label:<40} ready in {time.monotonic() - started:.0f}s  {document.id}"))
        return "ingested"
