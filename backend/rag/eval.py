"""Retrieval evaluation: recall@k and MRR over a hand-written query set.

Pure functions, so the metrics can be unit-tested without a database. The
`rag_eval` management command wires them to `hybrid_search`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

RECALL_KS = (5, 10)
MRR_K = 10


@dataclass
class EvalCase:
    query: str
    expected_document_id: str
    expected_pages: list[int] = field(default_factory=list)


def parse_cases(text: str) -> list[EvalCase]:
    """One JSON object per line: {"query", "expected_document_id", "expected_pages"?}."""
    cases: list[EvalCase] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append(
            EvalCase(
                query=str(row["query"]),
                expected_document_id=str(row["expected_document_id"]),
                expected_pages=[int(p) for p in row.get("expected_pages") or []],
            )
        )
    return cases


def _document_rank(case: EvalCase, hits: list[dict]) -> int | None:
    for rank, hit in enumerate(hits):
        if str(hit.get("document_id")) == case.expected_document_id:
            return rank
    return None


def _page_rank(case: EvalCase, hits: list[dict]) -> int | None:
    for rank, hit in enumerate(hits):
        if str(hit.get("document_id")) != case.expected_document_id:
            continue
        start = hit.get("page_start") or 0
        end = hit.get("page_end") or start
        if any(start <= page <= end for page in case.expected_pages):
            return rank
    return None


def evaluate(
    cases: list[EvalCase],
    search: Callable[[str], list[dict]],
    *,
    top_k: int = MRR_K,
) -> dict:
    """Run every case through `search` and aggregate the metrics."""
    doc_ranks: list[int | None] = []
    page_ranks: list[int | None] = []
    for case in cases:
        hits = list(search(case.query))[:top_k]
        doc_ranks.append(_document_rank(case, hits))
        if case.expected_pages:
            page_ranks.append(_page_rank(case, hits))

    def recall(ranks: list[int | None], k: int) -> float | None:
        if not ranks:
            return None
        return round(sum(1 for r in ranks if r is not None and r < k) / len(ranks), 4)

    report: dict = {"cases": len(cases), "top_k": top_k}
    for k in RECALL_KS:
        report[f"doc_recall@{k}"] = recall(doc_ranks, k)
        report[f"page_recall@{k}"] = recall(page_ranks, k)
    if doc_ranks:
        report[f"mrr@{MRR_K}"] = round(
            sum(1 / (r + 1) for r in doc_ranks if r is not None and r < MRR_K) / len(doc_ranks), 4
        )
    else:
        report[f"mrr@{MRR_K}"] = None
    return report
