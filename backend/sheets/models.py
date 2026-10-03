from __future__ import annotations

from django.db import models

from documents.models import Document


class Sheet(models.Model):
    """One worksheet of an uploaded spreadsheet (a CSV has exactly one).

    `rows` holds the parsed data rows, so previews, confirmation and indexing
    never re-read the file. `profile` describes each source column (inferred
    type, samples, ranges); `proposed_schema` is what the LLM (or the
    heuristic fallback) suggested, and `schema` what the user confirmed.
    """

    PENDING = "pending"
    PROPOSING = "proposing"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"

    document = models.ForeignKey(Document, related_name="sheets", on_delete=models.CASCADE)
    index = models.IntegerField()  # 0-based; the document "page" is index + 1
    name = models.CharField(max_length=255)
    # Table name in the per-document SQLite file.
    table_name = models.CharField(max_length=64)
    # 1-based spreadsheet row of the (last) header row; null when the sheet has none.
    header_row = models.IntegerField(null=True, blank=True)
    header_rows = models.IntegerField(default=1)
    row_count = models.IntegerField(default=0)
    col_count = models.IntegerField(default=0)

    headers = models.JSONField(default=list, blank=True)
    # [[spreadsheet_row_number, [value, ...]], ...]; values are JSON scalars.
    rows = models.JSONField(default=list, blank=True)
    profile = models.JSONField(default=list, blank=True)

    proposed_schema = models.JSONField(default=dict, blank=True)
    schema = models.JSONField(default=dict, blank=True)
    schema_status = models.CharField(max_length=16, default=PENDING)
    # "llm" or "heuristic": the UI says when the AI suggestion was unavailable.
    schema_source = models.CharField(max_length=16, blank=True)
    proposal_error = models.TextField(blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("document", "index")
        ordering = ["index"]

    def __str__(self) -> str:
        return f"{self.name} ({self.row_count} rows)"

    @property
    def active_schema(self) -> dict:
        return self.schema or self.proposed_schema
