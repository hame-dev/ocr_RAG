from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.db import models

from documents.models import Document


class ExtractionPlan(models.Model):
    """What the LLM says can be extracted from this document.

    This is the "qwen tells the user what information is available" step. In
    auto mode we accept everything above a confidence floor; in advanced mode
    the user's declared fields are merged in and marked required.
    """

    document = models.OneToOneField(
        Document, related_name="extraction_plan", on_delete=models.CASCADE
    )
    model_id = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, default="pending")
    detected_doc_type = models.CharField(max_length=64, blank=True)
    proposed_fields = models.JSONField(default=list, blank=True)
    accepted_fields = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class MetadataRecord(models.Model):
    """The standardized metadata for one document.

    Hot filter fields are denormalized into real columns (and onto each chunk's
    `meta` JSONB) so that RAG filtering never needs a join.
    """

    document = models.OneToOneField(
        Document, related_name="metadata", on_delete=models.CASCADE
    )
    schema_version = models.CharField(max_length=16, default="1.0")

    doc_type = models.CharField(max_length=48, db_index=True, blank=True)
    title = models.CharField(max_length=1024, blank=True)
    title_translit = models.CharField(max_length=1024, blank=True)
    primary_language = models.CharField(max_length=8, db_index=True, blank=True)
    languages = ArrayField(models.CharField(max_length=8), default=list, blank=True)
    summary_short = models.TextField(blank=True)
    summary_long = models.TextField(blank=True)
    keywords = ArrayField(models.CharField(max_length=96), default=list, blank=True)
    topics = ArrayField(models.CharField(max_length=96), default=list, blank=True)
    document_date = models.DateField(null=True, blank=True, db_index=True)

    entities = models.JSONField(default=dict, blank=True)
    dates = models.JSONField(default=dict, blank=True)
    identifiers = models.JSONField(default=dict, blank=True)
    provenance = models.JSONField(default=dict, blank=True)
    quality_flags = ArrayField(models.CharField(max_length=48), default=list, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)

    # The whole validated dump, so nothing is lost to denormalization.
    core = models.JSONField(default=dict, blank=True)

    model_id = models.CharField(max_length=128, blank=True)
    prompt_version = models.CharField(max_length=24, default="v1")
    validation_attempts = models.IntegerField(default=1)
    # True when the retry ladder bottomed out in the degraded core-only record.
    is_partial = models.BooleanField(default=False)
    raw_llm_output = models.TextField(blank=True)
    human_edited = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def filter_meta(self) -> dict:
        """Exactly the fields denormalized onto each chunk for filtering."""
        return {
            "doc_type": self.doc_type,
            "primary_language": self.primary_language,
            "languages": self.languages,
            "document_date": self.document_date.isoformat() if self.document_date else None,
            "keywords": self.keywords[:15],
            "topics": self.topics[:10],
            "title": self.title[:300],
            "ocr_engine": (self.provenance or {}).get("ocr_engine", ""),
            "ai_corrected": (self.provenance or {}).get("ai_corrected", False),
            "page_count": (self.provenance or {}).get("page_count"),
        }
