from __future__ import annotations

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models


class Conversation(models.Model):
    """A chat thread.

    `id` IS the LangGraph thread_id, so agent memory and this row stay in sync
    without a mapping table.

    `scope` is what makes "chat with THIS document later" work: a conversation
    opened from a document page is scoped to it, while /chat spans the library.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=512, blank=True)
    scope = models.CharField(max_length=16, default="all")  # all | selected
    document_ids = ArrayField(models.UUIDField(), default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    @property
    def thread_id(self) -> str:
        return str(self.id)

    def scoped_document_ids(self) -> list[str] | None:
        """None means "search everything"."""
        if self.scope == "selected":
            return [str(d) for d in self.document_ids]
        return None


class Message(models.Model):
    """Human-readable transcript for the UI.

    NOT the source of truth for agent memory — that lives in the LangGraph
    checkpointer tables. This exists so the UI can render history without
    replaying a graph.
    """

    seq = models.BigAutoField(primary_key=True)
    conversation = models.ForeignKey(
        Conversation, related_name="messages", on_delete=models.CASCADE
    )
    role = models.CharField(max_length=16)  # user | assistant | tool | system
    content = models.TextField(blank=True)
    tool_calls = models.JSONField(default=list, blank=True)
    citations = models.JSONField(default=list, blank=True)
    citation_mode = models.CharField(max_length=16, default="explicit")
    usage = models.JSONField(default=dict, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    model_id = models.CharField(max_length=128, blank=True)
    error = models.TextField(blank=True)
    # True when the client disconnected mid-stream; the partial answer is kept
    # rather than thrown away.
    is_partial = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["seq"]
