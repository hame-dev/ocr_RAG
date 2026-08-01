from __future__ import annotations

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from common import fsm


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=512, blank=True)
    original_filename = models.CharField(max_length=512)
    mime_type = models.CharField(max_length=128)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, db_index=True, blank=True)
    storage_path = models.CharField(max_length=1024)

    page_count = models.IntegerField(null=True, blank=True)

    # Digital-text detection. A PDF with a usable text layer skips OCR entirely.
    is_digital_pdf = models.BooleanField(null=True)
    digital_text_report = models.JSONField(default=dict, blank=True)
    detected_languages = ArrayField(
        models.CharField(max_length=8), default=list, blank=True
    )

    status = models.CharField(max_length=32, default=fsm.UPLOADED, db_index=True)
    status_detail = models.CharField(max_length=512, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)

    # auto: the LLM proposes which fields to extract.
    # advanced: the user declares required fields up front.
    metadata_mode = models.CharField(max_length=16, default="auto")
    required_fields = models.JSONField(default=list, blank=True)

    current_revision = models.ForeignKey(
        "documents.TextRevision",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title or self.original_filename} ({self.status})"

    @property
    def display_title(self) -> str:
        return self.title or self.original_filename


class DocumentPage(models.Model):
    document = models.ForeignKey(
        Document, related_name="pages", on_delete=models.CASCADE
    )
    page_number = models.IntegerField()  # 1-based everywhere
    width_px = models.IntegerField(default=0)
    height_px = models.IntegerField(default=0)

    # {"classical@300": "/data/media/.../p1.png", "neural@200": "..."} — rasters
    # are cached per profile and reused across engines and re-runs.
    raster_paths = models.JSONField(default=dict, blank=True)
    thumbnail_path = models.CharField(max_length=1024, blank=True)

    native_text = models.TextField(blank=True)
    native_char_count = models.IntegerField(default=0)

    rotation_deg = models.FloatField(default=0.0)  # OSD-detected, applied
    skew_deg = models.FloatField(default=0.0)  # fine deskew, applied
    preprocess_meta = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("document", "page_number")
        ordering = ["page_number"]


class DocumentEvent(models.Model):
    """Append-only event log. `seq` doubles as the SSE Last-Event-ID."""

    seq = models.BigAutoField(primary_key=True)
    document = models.ForeignKey(
        Document, related_name="events", on_delete=models.CASCADE
    )
    kind = models.CharField(max_length=48)
    from_status = models.CharField(max_length=32, blank=True)
    to_status = models.CharField(max_length=32, blank=True)
    actor = models.CharField(max_length=16, default="system")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["seq"]
        indexes = [models.Index(fields=["document", "seq"])]


class TextRevision(models.Model):
    """The versioning spine.

    Append-only. Raw OCR (OCRRun.text) is never mutated; every edit — manual or
    AI — creates a new revision with a parent pointer, so you can always get back
    to exactly what the engine produced.
    """

    SOURCE_CHOICES = [
        ("ocr_pick", "OCR selection"),
        ("manual_edit", "Manual edit"),
        ("ai_correction", "AI correction"),
        ("manual_entry", "Typed by hand"),
        ("merge", "Merged"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, related_name="revisions", on_delete=models.CASCADE
    )
    revision_no = models.IntegerField()
    source = models.CharField(max_length=24, choices=SOURCE_CHOICES)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    origin_run = models.ForeignKey(
        "ocr.OCRRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="revisions",
    )

    # Pages are separated by \f throughout the system: chunker, editor, diff.
    text = models.TextField(blank=True)
    char_count = models.IntegerField(default=0)
    word_count = models.IntegerField(default=0)
    diff_stats = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=512, blank=True)
    created_by = models.CharField(max_length=16, default="user")
    is_final = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("document", "revision_no")
        ordering = ["revision_no"]

    def save(self, *args, **kwargs):
        self.char_count = len(self.text)
        self.word_count = len(self.text.split())
        if self.parent_id and not self.diff_stats:
            from common.diffing import diff_stats

            self.diff_stats = diff_stats(self.parent.text, self.text)
        super().save(*args, **kwargs)

    @property
    def pages(self) -> list[str]:
        return self.text.split("\f")
