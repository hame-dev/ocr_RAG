"""Retrieval-evaluation metrics, on hand-built hit lists (no database)."""
from __future__ import annotations

from rag.eval import EvalCase, evaluate, parse_cases


def _hit(doc: str, page: int) -> dict:
    return {"document_id": doc, "page_start": page, "page_end": page}


def test_parse_cases_reads_jsonl_and_skips_blank_lines():
    text = (
        '{"query": "مدة العقد", "expected_document_id": "d1", "expected_pages": [3]}\n'
        "\n"
        '{"query": "rent", "expected_document_id": "d2"}\n'
    )
    cases = parse_cases(text)
    assert [c.query for c in cases] == ["مدة العقد", "rent"]
    assert cases[0].expected_pages == [3]
    assert cases[1].expected_pages == []


def test_recall_and_mrr_from_ranked_hits():
    cases = [
        EvalCase("q1", "d1", [2]),  # doc at rank 0, page matches at rank 0
        EvalCase("q2", "d2", [9]),  # doc at rank 6, right page never retrieved
        EvalCase("q3", "d3", []),  # never retrieved
    ]
    results = {
        "q1": [_hit("d1", 2), _hit("x", 1)],
        "q2": [_hit("x", 1)] * 6 + [_hit("d2", 4)],
        "q3": [_hit("x", 1)] * 3,
    }
    report = evaluate(cases, lambda q: results[q], top_k=10)

    assert report["cases"] == 3
    assert report["doc_recall@5"] == round(1 / 3, 4)
    assert report["doc_recall@10"] == round(2 / 3, 4)
    # Page recall only counts cases that name pages (q1, q2).
    assert report["page_recall@5"] == 0.5
    assert report["page_recall@10"] == 0.5
    assert report["mrr@10"] == round((1 / 1 + 1 / 7) / 3, 4)


def test_a_page_range_hit_counts_for_any_page_inside_it():
    cases = [EvalCase("q", "d1", [5])]
    hits = [{"document_id": "d1", "page_start": 4, "page_end": 6}]
    assert evaluate(cases, lambda q: hits)["page_recall@5"] == 1.0


def test_hits_beyond_top_k_are_ignored():
    cases = [EvalCase("q", "d1", [])]
    hits = [_hit("x", 1)] * 5 + [_hit("d1", 1)]
    report = evaluate(cases, lambda q: hits, top_k=5)
    assert report["doc_recall@10"] == 0.0
    assert report["mrr@10"] == 0.0
