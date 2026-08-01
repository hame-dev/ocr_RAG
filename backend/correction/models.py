from __future__ import annotations

import uuid

from django.db import models

from documents.models import Document, TextRevision


class AICorrectionJob(models.Model):
    """One AI proofreading pass over a revision.

    Corrections are NEVER auto-applied. They land here as proposals; the user
    accepts or rejects each one, and only then is a new revision created.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, related_name="correction_jobs", on_delete=models.CASCADE
    )
    base_revision = models.ForeignKey(TextRevision, on_delete=models.PROTECT)

    model_id = models.CharField(max_length=128, blank=True)
    prompt_version = models.CharField(max_length=24, default="v1")
    # Sending the page image alongside the text is the strongest guard against
    # invented content: the model can check the pixels.
    multimodal = models.BooleanField(default=True)

    status = models.CharField(max_length=16, default="queued")
    scope = models.CharField(max_length=16, default="all")
    pages = models.JSONField(default=list, blank=True)

    changes = models.JSONField(default=list, blank=True)
    rejected = models.JSONField(default=list, blank=True)
    guard_report = models.JSONField(default=dict, blank=True)

    result_revision = models.ForeignKey(
        TextRevision, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    duration_ms = models.IntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
