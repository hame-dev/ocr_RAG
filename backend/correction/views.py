from __future__ import annotations

from django.http import Http404
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from correction.guards import apply_changes
from correction.models import AICorrectionJob
from common.ownership import get_owned_document


class StartCorrectionSerializer(serializers.Serializer):
    multimodal = serializers.BooleanField(required=False, default=True)
    scope = serializers.ChoiceField(choices=["all", "pages"], required=False, default="all")
    pages = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, default=list, max_length=2000
    )


@api_view(["POST"])
def start_correction(request, document_id):
    document = get_owned_document(request.user, document_id)
    if not document.current_revision:
        return Response(
            {"detail": "select or enter document text first"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = StartCorrectionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    job = AICorrectionJob.objects.create(
        document=document,
        base_revision=document.current_revision,
        **serializer.validated_data,
    )

    from correction.tasks import run_ai_correction

    run_ai_correction.delay(str(job.id))
    return Response({"job_id": str(job.id), "status": "queued"},
                    status=status.HTTP_202_ACCEPTED)


@api_view(["GET"])
def correction_job(request, job_id):
    job = AICorrectionJob.objects.filter(id=job_id, document__owner=request.user).first()
    if not job:
        raise Http404("no such correction job")
    return Response(
        {
            "job_id": str(job.id),
            "status": job.status,
            "model_id": job.model_id,
            "multimodal": job.multimodal,
            "base_revision": job.base_revision.revision_no,
            "changes": job.changes,
            # Surfaced so the guards are auditable rather than a black box.
            "rejected": job.rejected,
            "guard_report": job.guard_report,
            "duration_ms": job.duration_ms,
            "error_message": job.error_message,
        }
    )


@api_view(["POST"])
def apply_correction(request, job_id):
    """Apply the changes the USER accepted, producing a new revision."""
    job = AICorrectionJob.objects.select_related("document", "base_revision").filter(
        id=job_id, document__owner=request.user
    ).first()
    if not job:
        raise Http404("no such correction job")
    if job.status != "ready":
        return Response(
            {"detail": f"job is {job.status}"}, status=status.HTTP_400_BAD_REQUEST
        )

    accepted_ids = request.data.get("accepted_ids")
    if accepted_ids is None:
        # Default to the pre-checked mechanical fixes only.
        chosen = [c for c in job.changes if c.get("auto_accept")]
    else:
        wanted = set(accepted_ids)
        chosen = [c for c in job.changes if c.get("id") in wanted]

    if not chosen:
        return Response(
            {"detail": "no changes selected"}, status=status.HTTP_400_BAD_REQUEST
        )

    pages = job.base_revision.pages
    by_page: dict[int, list[dict]] = {}
    for change in chosen:
        by_page.setdefault(change["page"], []).append(change)

    corrected = [
        apply_changes(text, by_page.get(index + 1, [])) for index, text in enumerate(pages)
    ]

    from documents.views import _next_revision

    revision = _next_revision(
        job.document,
        text="\f".join(corrected),
        source="ai_correction",
        parent=job.base_revision,
        note=f"AI correction: {len(chosen)} change(s) accepted",
    )
    job.result_revision = revision
    job.status = "applied"
    job.save(update_fields=["result_revision", "status"])

    from documents.serializers import TextRevisionDetailSerializer

    return Response(
        {
            "applied": len(chosen),
            "revision": TextRevisionDetailSerializer(revision).data,
        }
    )
