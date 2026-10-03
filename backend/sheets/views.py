"""The Columns step: see what the AI proposed, preview, adjust, confirm."""
from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common import fsm
from common.ownership import get_owned_document
from sheets.models import Sheet
from sheets.services import pipeline
from sheets.services.cards import preview_cards
from sheets.services.parse import SpreadsheetError
from sheets.services.schema import normalize

PREVIEW_ROWS = 3


def serialize_sheet(sheet: Sheet) -> dict:
    return {
        "index": sheet.index,
        "name": sheet.name,
        "table_name": sheet.table_name,
        "header_row": sheet.header_row,
        "header_rows": sheet.header_rows,
        "row_count": sheet.row_count,
        "col_count": sheet.col_count,
        "profile": sheet.profile,
        "proposed_schema": sheet.proposed_schema,
        "schema": sheet.schema,
        "schema_status": sheet.schema_status,
        "schema_source": sheet.schema_source,
        "proposal_error": sheet.proposal_error,
        "confirmed_at": sheet.confirmed_at,
        # The first rows as they are in the file, for the header-row picker.
        "first_rows": [{"row": n, "values": values} for n, values in sheet.rows[:PREVIEW_ROWS]],
        "preview": preview_cards(sheet, sheet.active_schema, PREVIEW_ROWS) if sheet.active_schema else [],
    }


def _spreadsheet(request, document_id):
    document = get_owned_document(request.user, document_id)
    if not pipeline.is_spreadsheet(document):
        return document, Response({"detail": "this document is not a spreadsheet"}, status=status.HTTP_400_BAD_REQUEST)
    return document, None


@api_view(["GET"])
def sheet_list(request, document_id):
    document, error = _spreadsheet(request, document_id)
    if error:
        return error
    return Response({"status": document.status, "sheets": [serialize_sheet(s) for s in document.sheets.all()]})


class PreviewSerializer(serializers.Serializer):
    sheet = serializers.IntegerField(min_value=0)
    schema = serializers.DictField()


@api_view(["POST"])
def sheet_preview(request, document_id):
    """Cards for an unsaved schema, normalized the way confirm would."""
    document, error = _spreadsheet(request, document_id)
    if error:
        return error
    body = PreviewSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    sheet = document.sheets.filter(index=body.validated_data["sheet"]).first()
    if sheet is None:
        return Response({"detail": "no such sheet"}, status=status.HTTP_404_NOT_FOUND)
    schema = normalize(body.validated_data["schema"], sheet.profile)
    return Response({"schema": schema, "preview": preview_cards(sheet, schema, PREVIEW_ROWS)})


class ReprofileSerializer(serializers.Serializer):
    sheet = serializers.IntegerField(min_value=0)
    header_row = serializers.IntegerField(min_value=1, allow_null=True)


@api_view(["POST"])
def sheet_reprofile(request, document_id):
    """Read a sheet again with its header on another row, and re-propose."""
    document, error = _spreadsheet(request, document_id)
    if error:
        return error
    if document.status not in (fsm.PREPROCESSED, fsm.READY, fsm.TEXT_FINALIZED, fsm.ENRICHED):
        return Response(
            {"detail": f"cannot change the header row while the document is {document.status}"},
            status=status.HTTP_409_CONFLICT,
        )
    body = ReprofileSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    sheet = document.sheets.filter(index=body.validated_data["sheet"]).first()
    if sheet is None:
        return Response({"detail": "no such sheet"}, status=status.HTTP_404_NOT_FOUND)
    try:
        sheet = pipeline.reprofile(sheet, body.validated_data["header_row"])
    except SpreadsheetError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    from sheets.tasks import propose_schema

    Sheet.objects.filter(id=sheet.id).update(schema_status=Sheet.PROPOSING)
    propose_schema.delay(str(document.id), sheet.index)
    sheet.refresh_from_db()
    return Response(serialize_sheet(sheet))


class ConfirmSheetSerializer(serializers.Serializer):
    index = serializers.IntegerField(min_value=0)
    schema = serializers.DictField()


class ConfirmSerializer(serializers.Serializer):
    sheets = ConfirmSheetSerializer(many=True, required=False, max_length=50)


@api_view(["POST"])
def sheet_confirm(request, document_id):
    """Adopt the column schemas and index the spreadsheet.

    Sheets left out keep their current (or proposed) schema.
    """
    document, error = _spreadsheet(request, document_id)
    if error:
        return error
    if not pipeline.can_confirm(document):
        return Response(
            {"detail": f"cannot confirm while the document is {document.status}; wait for it to finish"},
            status=status.HTTP_409_CONFLICT,
        )
    if not document.sheets.exists():
        return Response({"detail": "the spreadsheet has not been read yet"}, status=status.HTTP_409_CONFLICT)
    body = ConfirmSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    schemas = {item["index"]: item["schema"] for item in body.validated_data.get("sheets") or []}
    pipeline.confirm(document, schemas)
    document.refresh_from_db()
    return Response({"status": document.status, "sheets": [serialize_sheet(s) for s in document.sheets.all()]})
