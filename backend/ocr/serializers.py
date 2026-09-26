from rest_framework import serializers

from ocr.models import OCRBatch, OCRPageResult, OCRRun


class OCRRunSerializer(serializers.ModelSerializer):
    preview = serializers.CharField(read_only=True)

    class Meta:
        model = OCRRun
        fields = [
            "id", "engine_name", "engine_version", "model_id", "status",
            "preprocess_profile", "dpi", "mean_confidence", "gibberish_score",
            "arabic_char_ratio", "duration_ms", "char_count", "word_count",
            "warnings", "error_code", "error_message", "preview",
            "created_at", "finished_at",
        ]


class OCRRunDetailSerializer(OCRRunSerializer):
    class Meta(OCRRunSerializer.Meta):
        fields = OCRRunSerializer.Meta.fields + ["text"]


class OCRPageResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = OCRPageResult
        fields = ["page_number", "text", "confidence", "lines", "duration_ms", "warnings"]


class OCRBatchSerializer(serializers.ModelSerializer):
    runs = OCRRunSerializer(many=True, read_only=True)

    class Meta:
        model = OCRBatch
        fields = [
            "id", "document", "requested_engines", "languages", "options",
            "status", "runs", "created_at", "started_at", "finished_at",
        ]


class StartOCRSerializer(serializers.Serializer):
    engines = serializers.ListField(
        child=serializers.CharField(), allow_empty=False, min_length=1, required=False
    )
    languages = serializers.ListField(
        child=serializers.CharField(max_length=8), max_length=8, required=False
    )
    # Engine tuning. Engines read only the keys they know (tesseract: psm, oem)
    # and validate the values themselves.
    options = serializers.DictField(required=False)
    # Lets the user run an engine the health probe currently reports as down.
    force = serializers.BooleanField(required=False, default=False)
