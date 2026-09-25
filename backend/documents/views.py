from __future__ import annotations

import hashlib
import logging
import mimetypes
import os

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from common import fsm
from common.diffing import grapheme_opcodes
from documents.models import Document, DocumentPage, TextRevision
from documents.serializers import (
    CreateRevisionSerializer,
    DocumentDetailSerializer,
    DocumentEventSerializer,
    DocumentListSerializer,
    DocumentPageSerializer,
    FinalizeSerializer,
    SelectRunSerializer,
    TextRevisionDetailSerializer,
    TextRevisionSerializer,
    UploadSerializer,
)

logger = logging.getLogger(__name__)


class DocumentViewSet(viewsets.ModelViewSet):
    queryset = Document.objects.select_related("metadata", "current_revision").all()
    # Uploads use multipart, while revision/select/finalize/retry actions use
    # JSON from the frontend.  Restricting the whole viewset to form parsers
    # made every document mutation after upload return HTTP 415.
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filterset_fields = ["status", "metadata_mode"]

    def get_serializer_class(self):
        if self.action == "list":
            return DocumentListSerializer
        return DocumentDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset().filter(owner=self.request.user)
        params = self.request.query_params
        if statuses := params.get("status__in"):
            qs = qs.filter(status__in=[s for s in statuses.split(",") if s])
        if q := params.get("q"):
            qs = qs.filter(title__icontains=q) | qs.filter(original_filename__icontains=q)
        if doc_type := params.get("doc_type"):
            qs = qs.filter(metadata__doc_type=doc_type)
        if lang := params.get("lang"):
            qs = qs.filter(metadata__primary_language=lang)
        return qs.distinct()

    def create(self, request, *args, **kwargs):
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]

        document = Document.objects.create(
            owner=request.user,
            title=serializer.validated_data.get("title", ""),
            original_filename=upload.name,
            mime_type=(
                upload.content_type
                or mimetypes.guess_type(upload.name)[0]
                or "application/octet-stream"
            ),
            size_bytes=upload.size,
            metadata_mode=serializer.validated_data.get("metadata_mode", "auto"),
            required_fields=serializer.validated_data.get("required_fields", []),
            storage_path="",
        )

        target_dir = os.path.join(settings.MEDIA_ROOT, "docs", str(document.id))
        os.makedirs(target_dir, exist_ok=True)
        extension = os.path.splitext(upload.name)[1] or ".bin"
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

        transaction.on_commit(
            lambda: preprocess_document.delay(str(document.id), ["neural"])
        )

        return Response(
            DocumentDetailSerializer(document).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"])
    def pages(self, request, pk=None):
        document = self.get_object()
        return Response(
            DocumentPageSerializer(document.pages.all(), many=True).data
        )

    @action(detail=True, methods=["get"], url_path=r"pages/(?P<page_no>\d+)/image")
    def page_image(self, request, pk=None, page_no=None):
        document = self.get_object()
        page = document.pages.filter(page_number=int(page_no)).first()
        if not page:
            raise Http404("no such page")

        profile = request.query_params.get("profile", "neural")
        rasters = page.raster_paths or {}
        # Prefer the requested profile; fall back to whatever was rendered so the
        # viewer never shows a broken image.
        path = next(
            (p for key, p in rasters.items() if key.startswith(profile)),
            next(iter(rasters.values()), None),
        )
        if not path or not os.path.exists(path):
            raise Http404("page not rendered yet")
        return FileResponse(open(path, "rb"), content_type="image/png")

    @action(detail=True, methods=["get"], url_path="event-log")
    def events(self, request, pk=None):
        """Polling fallback for clients that cannot consume the SSE stream."""
        document = self.get_object()
        since = int(request.query_params.get("since", 0))
        events = document.events.filter(seq__gt=since)[:500]
        return Response(DocumentEventSerializer(events, many=True).data)

    # ---- text revisions -----------------------------------------------------

    @action(detail=True, methods=["get", "post"])
    def revisions(self, request, pk=None):
        document = self.get_object()

        if request.method == "GET":
            return Response(
                TextRevisionSerializer(document.revisions.all(), many=True).data
            )

        serializer = CreateRevisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        parent = document.current_revision
        if parent_id := serializer.validated_data.get("parent"):
            parent = document.revisions.filter(id=parent_id).first()

        revision = _next_revision(
            document,
            text=serializer.validated_data["text"],
            source="manual_edit" if parent else "manual_entry",
            parent=parent,
            note=serializer.validated_data.get("note", ""),
        )
        return Response(
            TextRevisionDetailSerializer(revision).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"], url_path=r"revisions/(?P<revision_no>\d+)")
    def revision_detail(self, request, pk=None, revision_no=None):
        document = self.get_object()
        revision = document.revisions.filter(revision_no=int(revision_no)).first()
        if not revision:
            raise Http404("no such revision")
        return Response(TextRevisionDetailSerializer(revision).data)

    @action(detail=True, methods=["get"], url_path="revisions/diff")
    def revision_diff(self, request, pk=None):
        document = self.get_object()
        a_no = int(request.query_params.get("a", 1))
        b_no = int(request.query_params.get("b", 2))
        a = document.revisions.filter(revision_no=a_no).first()
        b = document.revisions.filter(revision_no=b_no).first()
        if not a or not b:
            raise Http404("revision not found")
        return Response(
            {
                "a": a_no,
                "b": b_no,
                # Offsets are character offsets derived from grapheme-cluster
                # alignment, so the frontend can slice safely.
                "ops": [
                    {"tag": t, "a_start": a0, "a_end": a1, "b_start": b0, "b_end": b1}
                    for t, a0, a1, b0, b1 in grapheme_opcodes(a.text, b.text)
                ],
            }
        )

    @action(detail=True, methods=["post"], url_path="text/select")
    def select_text(self, request, pk=None):
        """Adopt one engine's output as revision 1. Raw OCR stays untouched."""
        document = self.get_object()
        serializer = SelectRunSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from ocr.models import OCRRun

        run = OCRRun.objects.filter(
            id=serializer.validated_data["run_id"], document=document
        ).first()
        if not run:
            raise Http404("no such OCR run for this document")
        if run.status != "succeeded":
            return Response(
                {"detail": f"run status is {run.status}; only succeeded runs can be selected"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        revision = _next_revision(
            document,
            text=run.text,
            source="ocr_pick",
            parent=None,
            origin_run=run,
            note=f"selected {run.engine_name}",
        )
        return Response(
            TextRevisionDetailSerializer(revision).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="text/finalize")
    def finalize_text(self, request, pk=None):
        document = self.get_object()
        serializer = FinalizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        revision_no = serializer.validated_data.get("revision_no")
        revision = (
            document.revisions.filter(revision_no=revision_no).first()
            if revision_no
            else document.current_revision
        )
        if not revision:
            return Response(
                {"detail": "no revision to finalize; select an OCR result or type text first"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        document.revisions.update(is_final=False)
        revision.is_final = True
        revision.save(update_fields=["is_final"])
        document.current_revision = revision
        document.save(update_fields=["current_revision", "updated_at"])

        fsm.transition_to(
            document, fsm.TEXT_FINALIZED,
            actor="user", payload={"revision_no": revision.revision_no},
        )
        return Response(TextRevisionDetailSerializer(revision).data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """Re-drive a failed stage without losing prior work."""
        document = self.get_object()
        stage = request.data.get("stage", "preprocess")

        if stage == "preprocess":
            from ocr.tasks import preprocess_document

            fsm.transition_to(document, fsm.UPLOADED, force=True)
            preprocess_document.delay(str(document.id), ["neural"])
        elif stage == "enrich":
            from enrichment.tasks import enrich_document

            enrich_document.delay(str(document.id))
        elif stage == "index":
            from rag.tasks import index_document

            index_document.delay(str(document.id))
        else:
            return Response(
                {"detail": f"unknown stage {stage!r}"}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response({"status": "queued", "stage": stage})


def _next_revision(document, *, text, source, parent=None, origin_run=None, note=""):
    """Append a revision. Revisions are never mutated in place."""
    with transaction.atomic():
        last = (
            TextRevision.objects.select_for_update()
            .filter(document=document)
            .order_by("-revision_no")
            .first()
        )
        revision = TextRevision.objects.create(
            document=document,
            revision_no=(last.revision_no + 1) if last else 1,
            source=source,
            parent=parent,
            origin_run=origin_run,
            text=text,
            note=note,
        )
        document.current_revision = revision
        document.save(update_fields=["current_revision", "updated_at"])

    fsm.record_event(
        document,
        "revision_created",
        {
            "revision_no": revision.revision_no,
            "source": source,
            "char_count": revision.char_count,
            "diff_stats": revision.diff_stats,
        },
        actor="user",
    )
    return revision
