from __future__ import annotations

import logging

from django.conf import settings
from django.http import Http404
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from common.ownership import get_owned_document
from ocr.engines import registry
from ocr.ranking import rank_key as _rank_key
from ocr.models import OCRBatch, OCRPageResult, OCRRun
from ocr.serializers import (
    OCRBatchSerializer,
    OCRRunDetailSerializer,
    StartOCRSerializer,
)

logger = logging.getLogger(__name__)


@api_view(["GET"])
def engine_catalog(request):
    """Health-probed engine list. The UI only offers available=True engines."""
    force = request.query_params.get("refresh") == "1"
    return Response(
        {
            "engines": registry.probe_all(force=force),
            "default_engine": settings.DEFAULT_OCR_ENGINE,
        }
    )


class OCRBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = OCRBatch.objects.prefetch_related("runs").all()
    serializer_class = OCRBatchSerializer

    def get_queryset(self):
        return super().get_queryset().filter(document__owner=self.request.user)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        batch = self.get_object()
        pending = batch.runs.filter(status__in=["queued", "running"])
        count = pending.update(status="cancelled")
        # get_object() came from a queryset that prefetches runs, so discard the
        # stale relation before deriving the post-update batch status.
        batch._prefetched_objects_cache.pop("runs", None)
        batch.status = batch.derive_status()
        batch.save(update_fields=["status"])
        return Response({"cancelled": count, "status": batch.status})

    @action(detail=True, methods=["get"])
    def compare(self, request, pk=None):
        """Page-aligned N-way comparison payload for the side-by-side grid."""
        batch = self.get_object()
        runs = list(batch.runs.filter(status="succeeded"))
        if not runs:
            return Response({"pages": [], "ranking": [], "baseline_run_id": None})

        ranked = sorted(runs, key=_rank_key, reverse=True)
        baseline = ranked[0]

        page_numbers = sorted(
            OCRPageResult.objects.filter(run__in=runs)
            .values_list("page_number", flat=True)
            .distinct()
        )

        by_run_page = {
            (str(r.run_id), r.page_number): r
            for r in OCRPageResult.objects.filter(run__in=runs)
        }

        from correction.consensus import locked_spans

        pages = []
        for page_number in page_numbers:
            columns = []
            texts = []
            for run in ranked:
                result = by_run_page.get((str(run.id), page_number))
                text = result.text if result else ""
                texts.append(text)
                columns.append(
                    {
                        "run_id": str(run.id),
                        "engine": run.engine_name,
                        "engine_version": run.engine_version,
                        "text": text,
                        "confidence": result.confidence if result else None,
                        "gibberish_score": run.gibberish_score,
                        "char_count": len(text),
                        "duration_ms": result.duration_ms if result else None,
                        "warnings": (result.warnings if result else []) + list(run.warnings),
                        "has_boxes": bool(result and result.lines),
                    }
                )

            spans, agreement = locked_spans(texts)
            pages.append(
                {
                    "page_number": page_number,
                    "columns": columns,
                    "consensus": {"locked_spans": spans, "agreement": agreement},
                }
            )

        return Response(
            {
                "batch_id": str(batch.id),
                "baseline_run_id": str(baseline.id),
                "ranking": [r.engine_name for r in ranked],
                "pages": pages,
            }
        )


class OCRRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = OCRRun.objects.prefetch_related("page_results").all()
    serializer_class = OCRRunDetailSerializer

    def get_queryset(self):
        return super().get_queryset().filter(document__owner=self.request.user)

    @action(detail=True, methods=["get"], url_path=r"pages/(?P<page_no>\d+)")
    def page(self, request, pk=None, page_no=None):
        run = self.get_object()
        result = run.page_results.filter(page_number=int(page_no)).first()
        if not result:
            raise Http404("no such page in this run")
        return Response(
            {
                "page_number": result.page_number,
                "text": result.text,
                "confidence": result.confidence,
                "lines": result.lines,
                "warnings": result.warnings,
            }
        )


@api_view(["POST"])
def start_ocr(request, document_id):
    """Kick off a multi-engine OCR batch."""
    document = get_owned_document(request.user, document_id)
    if document.is_spreadsheet:
        return Response(
            {"detail": "spreadsheets are read directly, not by OCR; confirm their columns instead"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = StartOCRSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    requested = serializer.validated_data.get("engines") or [settings.DEFAULT_OCR_ENGINE]

    known = set(registry.names())
    unknown = [e for e in requested if e not in known]
    if unknown:
        return Response(
            {"detail": f"unknown engines: {', '.join(unknown)}", "known": sorted(known)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Block engines that are down now rather than letting the batch fail later.
    healthy = {e["name"] for e in registry.probe_all() if e["available"]}
    unhealthy = [e for e in requested if e not in healthy]
    if unhealthy and not serializer.validated_data.get("force"):
        return Response(
            {
                "detail": f"these engines are not available: {', '.join(unhealthy)}",
                "unavailable": unhealthy,
            },
            status=status.HTTP_409_CONFLICT,
        )

    from ocr.tasks import start_ocr_batch

    batch = start_ocr_batch(
        document,
        engines=requested,
        languages=serializer.validated_data.get("languages") or ["ara", "eng"],
        options=serializer.validated_data.get("options") or {},
    )
    return Response(
        OCRBatchSerializer(batch).data, status=status.HTTP_202_ACCEPTED
    )
