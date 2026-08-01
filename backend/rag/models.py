from __future__ import annotations

import uuid

from django.db import models
from pgvector.django import HnswIndex, VectorField

from documents.models import Document, TextRevision


class Chunk(models.Model):
    """A retrievable unit.

    `text_norm` and `tsv` are GENERATED columns backed by ar_normalize_v1 — see
    the migration for why the function name is versioned.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, related_name="chunks", on_delete=models.CASCADE)
    revision = models.ForeignKey(
        TextRevision, related_name="chunks", on_delete=models.CASCADE
    )
    chunk_index = models.IntegerField()
    page_start = models.IntegerField(null=True, blank=True)
    page_end = models.IntegerField(null=True, blank=True)
    section_path = models.TextField(blank=True, default="")
    lang = models.CharField(max_length=8, default="mixed")
    text = models.TextField()
    token_count = models.IntegerField(default=0)
    char_count = models.IntegerField(default=0)
    # Denormalized filter fields, so search never joins to metadata.
    meta = models.JSONField(default=dict, blank=True)
    embedding = VectorField(dimensions=1024, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rag_chunk"
        unique_together = ("revision", "chunk_index")
        indexes = [
            models.Index(fields=["document", "chunk_index"]),
            HnswIndex(
                name="chunk_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=200,
                opclasses=["vector_cosine_ops"],
            ),
        ]


class IndexRun(models.Model):
    document = models.ForeignKey(Document, related_name="index_runs", on_delete=models.CASCADE)
    revision = models.ForeignKey(TextRevision, on_delete=models.CASCADE)
    embedding_model = models.CharField(max_length=64)
    dims = models.IntegerField(default=1024)
    chunk_count = models.IntegerField(default=0)
    status = models.CharField(max_length=16, default="queued")
    duration_ms = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
