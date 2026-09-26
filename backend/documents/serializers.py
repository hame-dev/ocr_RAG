from django.conf import settings
from rest_framework import serializers

from documents.models import Document, DocumentEvent, DocumentPage, TextRevision


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
    doc_type = serializers.SerializerMethodField()
    summary_short = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id", "display_title", "title", "original_filename", "mime_type",
            "size_bytes", "page_count", "status", "status_detail", "is_digital_pdf",
            "detected_languages", "metadata_mode", "doc_type", "summary_short",
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


def sniff_mime(upload) -> str | None:
    """The upload's real type from its content, or None if it is unsupported.

    Preprocessing branches on mime_type, so it must never come from the
    client's Content-Type or the file extension.
    """
    head = upload.read(8)
    upload.seek(0)
    if head.startswith(b"%PDF-"):
        return "application/pdf"

    from PIL import Image

    try:
        with Image.open(upload) as image:
            image.verify()
            fmt = image.format
    except Exception:
        return None
    finally:
        upload.seek(0)
    return Image.MIME.get(fmt) if fmt in IMAGE_FORMATS else None


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
        mime = sniff_mime(upload)
        if mime is None:
            raise serializers.ValidationError("only PDF and image files are supported")
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
