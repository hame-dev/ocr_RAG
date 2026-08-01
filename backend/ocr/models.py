from __future__ import annotations

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from documents.models import Document


class OCRBatch(models.Model):
    """One user request to run N engines over a document."""

    STATUS = ["queued", "running", "partial", "done", "failed", "cancelled"]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, related_name="ocr_batches", on_delete=models.CASCADE
    )
    requested_engines = ArrayField(models.CharField(max_length=48), default=list)
    languages = ArrayField(models.CharField(max_length=8), default=list, blank=True)
    options = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, default="queued", db_index=True)
    celery_group_id = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def derive_status(self) -> str:
        """Batch status is derived from its runs, never set independently.

        'partial' is what makes the UI feel alive: as soon as one engine
        succeeds the user can start reading it while the others are still going.
        """
        runs = list(self.runs.all())
        if not runs:
            return "queued"
        if any(r.status in ("queued", "running") for r in runs):
            return "partial" if any(r.status == "succeeded" for r in runs) else "running"
        if any(r.status == "succeeded" for r in runs):
            return "done"
        if all(r.status == "cancelled" for r in runs):
            return "cancelled"
        return "failed"


class OCRRun(models.Model):
    """One engine's attempt. `text` is IMMUTABLE once written."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(OCRBatch, related_name="runs", on_delete=models.CASCADE)
    document = models.ForeignKey(
        Document, related_name="ocr_runs", on_delete=models.CASCADE
    )

    engine_name = models.CharField(max_length=48, db_index=True)
    engine_version = models.CharField(max_length=64, blank=True)
    model_id = models.CharField(max_length=128, blank=True)

    status = models.CharField(max_length=16, default="queued", db_index=True)
    preprocess_profile = models.CharField(max_length=24, blank=True)
    dpi = models.IntegerField(null=True, blank=True)

    text = models.TextField(blank=True)
    mean_confidence = models.FloatField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    char_count = models.IntegerField(default=0)
    word_count = models.IntegerField(default=0)

    # Model-free quality signals used to rank the comparison grid.
    arabic_char_ratio = models.FloatField(null=True, blank=True)
    gibberish_score = models.FloatField(null=True, blank=True)

    warnings = models.JSONField(default=list, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("batch", "engine_name")
        ordering = ["engine_name"]

    @property
    def is_terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "timeout", "cancelled", "skipped")

    @property
    def preview(self) -> str:
        return (self.text or "")[:400]


class OCRPageResult(models.Model):
    run = models.ForeignKey(OCRRun, related_name="page_results", on_delete=models.CASCADE)
    page_number = models.IntegerField()
    text = models.TextField(blank=True)
    confidence = models.FloatField(null=True, blank=True)
    # Boxes live in JSONB: they are rendered, never joined on.
    lines = models.JSONField(default=list, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("run", "page_number")
        ordering = ["page_number"]


class EngineHealthSnapshot(models.Model):
    engine_name = models.CharField(max_length=48, db_index=True)
    available = models.BooleanField(default=False)
    version = models.CharField(max_length=64, blank=True)
    detail = models.CharField(max_length=512, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    checked_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-checked_at"]
