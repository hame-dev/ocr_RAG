from django.conf import settings
from rest_framework import serializers

from documents.models import Document, DocumentEvent, DocumentPage, TextRevision
from sheets.services.parse import (
    CSV_MIME,
    SPREADSHEET_MIMES,
    XLSX_MIME,
    SpreadsheetError,
    is_xlsx,
    looks_like_csv,
)


class DocumentPageSerializer(serializers.ModelSerializer):
    has_raster = serializers.SerializerMethodField()

    class Meta:
        model = DocumentPage
        fields = [
            "page_number", "width_px", "height_px", "native_char_count",
            "rotation_deg", "skew_deg", "has_raster",
        ]

    def get_has_raster(self, obj) -> bool:
        return bool(obj.raster_paths)


class TextRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TextRevision
        fields = [
            "id", "revision_no", "source", "parent", "origin_run", "char_count",
            "word_count", "diff_stats", "note", "created_by", "is_final", "created_at",
        ]


class TextRevisionDetailSerializer(TextRevisionSerializer):
    class Meta(TextRevisionSerializer.Meta):
        fields = TextRevisionSerializer.Meta.fields + ["text"]


class DocumentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentEvent
        fields = ["seq", "kind", "from_status", "to_status", "actor", "payload", "created_at"]


class DocumentListSerializer(serializers.ModelSerializer):
    display_title = serializers.CharField(read_only=True)
    is_spreadsheet = serializers.BooleanField(read_only=True)
    doc_type = serializers.SerializerMethodField()
    summary_short = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id", "display_title", "title", "original_filename", "mime_type",
            "size_bytes", "page_count", "status", "status_detail", "is_digital_pdf",
            "detected_languages", "metadata_mode", "doc_type", "summary_short", "is_spreadsheet",
            "created_at", "updated_at",
        ]

    def get_doc_type(self, obj) -> str:
        record = getattr(obj, "metadata", None)
        return record.doc_type if record else ""

    def get_summary_short(self, obj) -> str:
        record = getattr(obj, "metadata", None)
        return record.summary_short if record else ""


class DocumentDetailSerializer(DocumentListSerializer):
    pages = DocumentPageSerializer(many=True, read_only=True)
    current_revision_no = serializers.SerializerMethodField()
    revision_count = serializers.SerializerMethodField()

    class Meta(DocumentListSerializer.Meta):
        fields = DocumentListSerializer.Meta.fields + [
            "digital_text_report", "error_code", "error_message", "required_fields",
            "pages", "current_revision_no", "revision_count",
        ]

    def get_current_revision_no(self, obj):
        return obj.current_revision.revision_no if obj.current_revision else None

    def get_revision_count(self, obj) -> int:
        return obj.revisions.count()


# Image formats OpenCV can rasterize in preprocessing.
IMAGE_FORMATS = {"PNG", "JPEG", "TIFF", "WEBP", "BMP"}


# The highest DPI any preprocessing profile renders at (ocr/preprocess/profiles.py).
MAX_RENDER_DPI = 300


def _too_big(upload, mime: str) -> str | None:
    """Why this file would be too expensive to preprocess, or None.

    The byte limit alone does not bound the work: a small PDF can have
    thousands of pages or a poster-sized page, and a small PNG can declare
    huge dimensions. Every page is rasterized, so these are checked up front.
    """
    if mime in SPREADSHEET_MIMES:
        return None  # read in full by preprocessing, which enforces the sheet limits
    max_pages = settings.MAX_UPLOAD_PAGES
    max_pixels = settings.MAX_PAGE_PIXELS
    try:
        if mime == "application/pdf":
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(upload.read())
            try:
                if len(pdf) > max_pages:
                    return f"the PDF has {len(pdf)} pages; the limit is {max_pages}"
                scale = MAX_RENDER_DPI / 72.0
                for index in range(len(pdf)):
                    width, height = pdf.get_page_size(index)
                    if width * scale * height * scale > max_pixels:
                        return f"page {index + 1} is too large to process"
            finally:
                pdf.close()
        else:
            from PIL import Image

            with Image.open(upload) as image:
                width, height = image.size
            if width * height > max_pixels:
                return "the image is too large to process"
    except Exception:
        return None  # unreadable here: preprocessing reports it properly
    finally:
        upload.seek(0)
    return None


def sniff_mime(upload) -> str | None:
    """The upload's real type from its content, or None if it is unsupported.

    Preprocessing branches on mime_type, so it must never come from the
    client's Content-Type or the file extension.
    """
    head = upload.read(8)
    upload.seek(0)
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK"):
        # A zip is only accepted as an Excel workbook. Read in full: a zip's
        # directory is at its end. The size check has already run.
        data = upload.read()
        upload.seek(0)
        if is_xlsx(data, settings.MAX_SHEET_UNCOMPRESSED_BYTES):
            return XLSX_MIME
        return None

    from PIL import Image

    try:
        with Image.open(upload) as image:
            image.verify()
            fmt = image.format
    except Exception:
        fmt = None
    finally:
        upload.seek(0)
    if fmt is not None:
        return Image.MIME.get(fmt) if fmt in IMAGE_FORMATS else None

    # Not an image: plain text with a consistent delimiter is a CSV.
    is_csv = looks_like_csv(upload.read(65536))
    upload.seek(0)
    return CSV_MIME if is_csv else None


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    title = serializers.CharField(required=False, allow_blank=True, max_length=512)
    metadata_mode = serializers.ChoiceField(
        choices=["auto", "advanced"], required=False, default="auto"
    )
    required_fields = serializers.ListField(
        child=serializers.JSONField(), required=False, max_length=50
    )

    def validate_file(self, upload):
        limit = settings.MAX_UPLOAD_BYTES
        if upload.size > limit:
            raise serializers.ValidationError(
                f"file is larger than {limit // (1024 * 1024)} MB"
            )
        try:
            mime = sniff_mime(upload)
        except SpreadsheetError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        if mime is None:
            raise serializers.ValidationError(
                "only PDF, image, Excel (.xlsx) and CSV files are supported"
            )
        if reason := _too_big(upload, mime):
            raise serializers.ValidationError(reason)
        upload.sniffed_mime = mime
        return upload


class CreateRevisionSerializer(serializers.Serializer):
    text = serializers.CharField(allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)
    parent = serializers.UUIDField(required=False, allow_null=True)


class SelectRunSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()


class FinalizeSerializer(serializers.Serializer):
    revision_no = serializers.IntegerField(required=False)
