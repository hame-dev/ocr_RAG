"""Re-index every document so its chunks carry the current per-chunk metadata.

    python manage.py reindex_all                      # everything with finalized text
    python manage.py reindex_all --only-missing       # chunks indexed before meta["chunk"]
    python manage.py reindex_all --contextualize-only # stage 2 only, no re-embedding of stage 1

Stage 1 runs on the `index` queue and takes seconds per document; stage 2 is
cached, so re-running it on unchanged documents makes no LLM calls.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Q

from common import fsm
from documents.models import Document
from rag.models import Chunk

INDEXABLE = (fsm.ENRICHED, fsm.INDEXED, fsm.READY)


class Command(BaseCommand):
    help = "Queue every indexable document for re-indexing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-missing", action="store_true",
            help="Only documents with chunks that predate per-chunk metadata.",
        )
        parser.add_argument(
            "--contextualize-only", action="store_true",
            help="Queue stage-2 contextualization only (documents must already be indexed).",
        )

    def handle(self, *args, **options):
        docs = Document.objects.filter(status__in=INDEXABLE, current_revision__isnull=False)
        if options["only_missing"]:
            missing = Chunk.objects.filter(~Q(meta__has_key="chunk")).values("document_id")
            docs = docs.filter(id__in=missing)
        doc_ids = [str(d) for d in docs.order_by("created_at").values_list("id", flat=True)]

        if options["contextualize_only"]:
            from rag.tasks import queue_contextualize

            queued = sum(1 for doc_id in doc_ids if queue_contextualize(doc_id, force=True))
            self.stdout.write(self.style.SUCCESS(f"queued stage-2 contextualization for {queued} document(s)"))
            return

        from rag.tasks import index_document

        for doc_id in doc_ids:
            index_document.delay(doc_id)
        self.stdout.write(self.style.SUCCESS(f"queued {len(doc_ids)} document(s) for re-indexing"))
