from __future__ import annotations

import uuid

from django.db import models
from pgvector.django import HnswIndex, VectorField

from documents.models import Document, TextRevision


class Chunk(models.Model):
    """A retrievable unit.

    `text_norm` and `tsv` are GENERATED columns backed by ar_normalize_v1 — see
    the migrations for why the function name is versioned. `tsv` is weighted:
    keywords_text (A), context_text (B), text (C), title_text (D).

    `text` is always the raw finalized text, so citations and quotes are exact;
    the contextual header exists only in the embedding. Per-chunk metadata
    lives under `meta["chunk"]`, beside the inherited document keys.
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
    # Lexical-search inputs, rebuilt whenever the chunk's metadata changes.
    # db_default so a worker still on pre-0003 code (which omits them) can
    # insert while the migration is already applied.
    keywords_text = models.TextField(blank=True, default="", db_default="")
    context_text = models.TextField(blank=True, default="", db_default="")
    title_text = models.TextField(blank=True, default="", db_default="")
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
    # Stage 2 (background contextualization): none, queued, running,
    # succeeded, failed or aborted. Never affects the document's status.
    context_status = models.CharField(max_length=16, default="none")
    context_windows = models.IntegerField(default=0)
    context_done = models.IntegerField(default=0)
    context_model = models.CharField(max_length=128, blank=True)
    context_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ChunkContext(models.Model):
    """Cache of stage-2 LLM output, keyed by content, so a re-index is free.

    `text_sha256` hashes the window's title, section and text (see
    rag.contextualize.window_key), not a single chunk.
    """

    text_sha256 = models.CharField(max_length=64)
    model_id = models.CharField(max_length=128)
    prompt_version = models.CharField(max_length=24)
    summary = models.TextField(blank=True)
    keywords = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("text_sha256", "model_id", "prompt_version")


class DocumentVector(models.Model):
    """One embedding per document (title, summary, keywords, topics), for
    "which documents are about X" questions that chunk search answers badly."""

    document = models.OneToOneField(Document, related_name="vector", on_delete=models.CASCADE)
    embedding = VectorField(dimensions=1024)
    text_sha256 = models.CharField(max_length=64)
    model = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)
