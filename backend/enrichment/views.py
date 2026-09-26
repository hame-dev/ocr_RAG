from __future__ import annotations

from django.http import Http404
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.ownership import get_owned_document
from enrichment import extractor
from enrichment.models import ExtractionPlan, MetadataRecord
from enrichment.schemas import JSON_TYPE_MAP, build_extraction_schema, core_schema


class EnrichSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=["auto", "advanced"], required=False)
    required_fields = serializers.ListField(
        child=serializers.DictField(), required=False, max_length=50
    )
    auto_index = serializers.BooleanField(required=False, default=True)


def _string_list(max_length: int):
    return serializers.ListField(
        child=serializers.CharField(max_length=max_length, allow_blank=True),
        required=False, max_length=200,
    )


class MetadataPatchSerializer(serializers.Serializer):
    """Only the human-editable fields, each with the type its column stores."""
    doc_type = serializers.CharField(max_length=48, allow_blank=True, required=False)
    title = serializers.CharField(max_length=1024, allow_blank=True, required=False)
    title_translit = serializers.CharField(max_length=1024, allow_blank=True, required=False)
    primary_language = serializers.CharField(max_length=8, allow_blank=True, required=False)
    languages = _string_list(8)
    summary_short = serializers.CharField(allow_blank=True, required=False)
    summary_long = serializers.CharField(allow_blank=True, required=False)
    keywords = _string_list(96)
    topics = _string_list(96)
    entities = serializers.DictField(required=False)
    dates = serializers.DictField(required=False)
    identifiers = serializers.DictField(required=False)
    custom_fields = serializers.DictField(required=False)


def _document(request, document_id):
    return get_owned_document(request.user, document_id)


@api_view(["GET"])
def metadata_schema(request):
    """The standardized schema. The frontend renders its form from this."""
    return Response(
        {
            "schema_version": "1.0",
            "core": core_schema(),
            "custom_field_types": sorted(JSON_TYPE_MAP),
            "doc_types": core_schema()["properties"]["doc_type"]["enum"],
        }
    )


@api_view(["GET", "POST", "PATCH"])
def extraction_plan(request, document_id):
    """GET the proposal, POST to (re)generate it, PATCH to accept a subset."""
    document = _document(request, document_id)

    if request.method == "POST":
        from enrichment.tasks import plan_extraction

        plan_extraction.delay(str(document.id))
        return Response({"status": "queued"}, status=status.HTTP_202_ACCEPTED)

    plan = getattr(document, "extraction_plan", None)

    if request.method == "PATCH":
        if not plan:
            plan = ExtractionPlan.objects.create(document=document)
        accepted_keys = request.data.get("accepted_keys")
        if accepted_keys is not None:
            plan.accepted_fields = [
                f for f in plan.proposed_fields if f["key"] in set(accepted_keys)
            ]
        # Advanced mode: the user declares fields the LLM did not propose.
        if extra := request.data.get("extra_fields"):
            plan.accepted_fields = extractor.merge_required_fields(
                plan.accepted_fields, extra
            )
        plan.save()

    if not plan:
        return Response({"status": "pending", "proposed_fields": [], "accepted_fields": []})

    return Response(
        {
            "status": plan.status,
            "detected_doc_type": plan.detected_doc_type,
            "proposed_fields": plan.proposed_fields,
            "accepted_fields": plan.accepted_fields,
            "error_message": plan.error_message,
            # Lets the UI show exactly what will be asked of the model.
            "resulting_schema": build_extraction_schema(plan.accepted_fields),
        }
    )


@api_view(["POST"])
def enrich(request, document_id):
    document = _document(request, document_id)

    if not document.current_revision:
        return Response(
            {"detail": "finalize the document text before enriching"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = EnrichSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if mode := data.get("mode"):
        document.metadata_mode = mode
    if required := data.get("required_fields"):
        document.required_fields = required
    document.save(update_fields=["metadata_mode", "required_fields", "updated_at"])

    from enrichment.tasks import enrich_document

    enrich_document.delay(str(document.id), auto_index=data["auto_index"])
    return Response({"status": "queued"}, status=status.HTTP_202_ACCEPTED)


@api_view(["GET", "PATCH"])
def metadata(request, document_id):
    document = _document(request, document_id)
    record = getattr(document, "metadata", None)
    if not record:
        raise Http404("this document has no metadata record yet")

    if request.method == "PATCH":
        serializer = MetadataPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changes = serializer.validated_data
        for key, value in changes.items():
            setattr(record, key, value)
        if "dates" in changes:
            record.document_date = extractor.parse_date(changes["dates"].get("document_date"))
        # Marks the record as human-authored so the UI can show provenance and
        # re-enrichment does not silently overwrite a person's corrections.
        record.human_edited = True
        record.save()

    return Response(_serialize(record))


def _serialize(record: MetadataRecord) -> dict:
    return {
        "schema_version": record.schema_version,
        "doc_type": record.doc_type,
        "title": record.title,
        "title_translit": record.title_translit,
        "primary_language": record.primary_language,
        "languages": record.languages,
        "summary_short": record.summary_short,
        "summary_long": record.summary_long,
        "keywords": record.keywords,
        "topics": record.topics,
        "document_date": record.document_date,
        "entities": record.entities,
        "dates": record.dates,
        "identifiers": record.identifiers,
        "provenance": record.provenance,
        "quality_flags": record.quality_flags,
        "custom_fields": record.custom_fields,
        "is_partial": record.is_partial,
        "human_edited": record.human_edited,
        "validation_attempts": record.validation_attempts,
        "model_id": record.model_id,
        "updated_at": record.updated_at,
    }
