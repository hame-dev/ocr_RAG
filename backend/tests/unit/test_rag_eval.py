"""Retrieval evaluation metrics: recall@k and MRR over hand-built hit lists."""
from __future__ import annotations

from rag.eval import EvalCase, evaluate, parse_cases


def _hit(document_id: str, page_start: int, page_end: int | None = None) -> dict:
    return {"document_id": document_id, "page_start": page_start, "page_end": page_end or page_start}


def test_document_recall_counts_a_hit_within_k():
    cases = [
        EvalCase(query="rent", expected_document_id="doc-a"),
        EvalCase(query="term", expected_document_id="doc-b"),
    ]
    results = {
        "rent": [_hit("doc-x", 1), _hit("doc-a", 2)],   # rank 2
        "term": [_hit("doc-x", 1)] * 6 + [_hit("doc-b", 1)],  # rank 7
    }
    report = evaluate(cases, lambda q: results[q], top_k=10)
    assert report["doc_recall@5"] == 0.5
    assert report["doc_recall@10"] == 1.0
    assert report["cases"] == 2


def test_page_recall_requires_the_expected_page_inside_the_hit_span():
    cases = [EvalCase(query="q", expected_document_id="doc-a", expected_pages=[4])]
    hits = [_hit("doc-a", 1, 2), _hit("doc-a", 3, 5)]  # page 4 is in the second hit
    report = evaluate(cases, lambda q: hits, top_k=10)
    assert report["doc_recall@5"] == 1.0
    assert report["page_recall@5"] == 1.0

    miss = [_hit("doc-a", 1, 2)]
    report = evaluate(cases, lambda q: miss, top_k=10)
    assert report["page_recall@5"] == 0.0


def test_mrr_uses_reciprocal_of_first_document_rank():
    cases = [
        EvalCase(query="first", expected_document_id="doc-a"),
        EvalCase(query="third", expected_document_id="doc-a"),
        EvalCase(query="absent", expected_document_id="doc-a"),
    ]
    results = {
        "first": [_hit("doc-a", 1)],
        "third": [_hit("doc-x", 1), _hit("doc-y", 1), _hit("doc-a", 1)],
        "absent": [_hit("doc-x", 1)],
    }
    report = evaluate(cases, lambda q: results[q], top_k=10)
    assert report["mrr@10"] == round((1 + 1 / 3 + 0) / 3, 4)


def test_page_metrics_are_null_when_no_case_has_pages():
    cases = [EvalCase(query="q", expected_document_id="doc-a")]
    report = evaluate(cases, lambda q: [_hit("doc-a", 1)], top_k=10)
    assert report["page_recall@5"] is None


def test_parse_cases_reads_jsonl_and_skips_blank_lines():
    text = '{"query": "a", "expected_document_id": "d1"}\n\n{"query": "b", "expected_document_id": "d2", "expected_pages": [2, 3]}\n'
    cases = parse_cases(text)
    assert [c.query for c in cases] == ["a", "b"]
    assert cases[1].expected_pages == [2, 3]
    assert cases[0].expected_pages == []
