"""Reranking: yes/no scoring from Ollama logprobs, ordering, and fallbacks."""
from __future__ import annotations

import math
import time

import pytest

from common.ollama import OllamaClient
from rag import chunking, rerank


class _Response:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _logprobs(chosen: tuple[str, float], top: list[tuple[str, float]]) -> dict:
    return {"response": chosen[0], "logprobs": [{
        "token": chosen[0], "logprob": chosen[1],
        "top_logprobs": [{"token": t, "logprob": lp} for t, lp in top],
    }]}


@pytest.fixture
def post(monkeypatch):
    calls = []

    def install(body):
        def fake_post(url, json=None, timeout=None):
            calls.append({"url": url, "json": json, "timeout": timeout})
            return _Response(body)

        monkeypatch.setattr("common.ollama.httpx.post", fake_post)
        return calls

    return install


def test_score_sums_every_spelling_of_yes_and_no(post):
    calls = post(_logprobs(("yes", math.log(0.5)), [
        ("yes", math.log(0.5)), ("Yes", math.log(0.2)), (" yes", math.log(0.1)), ("no", math.log(0.2)),
    ]))
    score = OllamaClient("http://ollama").score_yes_no("reranker", "prompt")
    assert score == pytest.approx(0.8 / 1.0)

    body = calls[0]["json"]
    assert calls[0]["url"].endswith("/api/generate")
    assert body["raw"] is True and body["logprobs"] is True and body["top_logprobs"] == 10
    assert body["options"] == {"num_predict": 1, "temperature": 0, "num_ctx": 2048}
    assert calls[0]["timeout"] == 5.0


def test_only_no_scores_zero(post):
    post(_logprobs(("no", math.log(0.9)), [("no", math.log(0.9)), ("maybe", math.log(0.1))]))
    assert OllamaClient("http://ollama").score_yes_no("reranker", "prompt") == 0.0


def test_neither_yes_nor_no_scores_zero(post):
    post(_logprobs(("hmm", math.log(0.9)), [("hmm", math.log(0.9))]))
    assert OllamaClient("http://ollama").score_yes_no("reranker", "prompt") == 0.0


def test_the_chosen_token_counts_even_outside_top_logprobs(post):
    post(_logprobs(("yes", math.log(0.6)), [("no", math.log(0.2))]))
    assert OllamaClient("http://ollama").score_yes_no("reranker", "prompt") == pytest.approx(0.75)


# ---- rerank() ----------------------------------------------------------------

def _hits(n: int) -> list[dict]:
    return [
        {"chunk_id": f"c{i}", "document_title": "Lease", "section_path": "", "meta": {},
         "text": f"passage {i}", "score": 1.0 / (i + 1)}
        for i in range(n)
    ]


class _Scorer:
    def __init__(self, scores: dict[str, float], *, sleep: dict[str, float] | None = None, fail: set | None = None):
        self.scores, self.sleep, self.fail = scores, sleep or {}, fail or set()
        self.prompts: list[str] = []

    def has_model(self, model):
        return True

    def score_yes_no(self, model, prompt, *, timeout=5.0, num_ctx=2048):
        self.prompts.append(prompt)
        key = next(k for k in self.scores if f"passage {k[1:]}<" in prompt)
        if key in self.fail:
            raise RuntimeError("boom")
        time.sleep(self.sleep.get(key, 0))
        return self.scores[key]


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(chunking, "get_tokenizer", lambda: None)
    rerank._availability.clear()


def test_rerank_orders_by_score_and_keeps_rrf(monkeypatch):
    scorer = _Scorer({"c0": 0.1, "c1": 0.9, "c2": 0.5})
    monkeypatch.setattr(rerank, "get_client", lambda: scorer)
    out = rerank.rerank("rent?", _hits(3), top_k=2, model="m")
    assert [h["chunk_id"] for h in out] == ["c1", "c2"]
    assert out[0]["score"] == 0.9 and out[0]["rrf"] == 0.5
    assert "<Query>: rent?" in scorer.prompts[0]
    assert scorer.prompts[0].endswith("<think>\n\n</think>\n\n")


def test_a_failed_pair_keeps_its_rrf_place_after_the_scored_ones(monkeypatch):
    scorer = _Scorer({"c0": 0.0, "c1": 0.2, "c2": 0.9}, fail={"c0"})
    monkeypatch.setattr(rerank, "get_client", lambda: scorer)
    out = rerank.rerank("q", _hits(3), top_k=3, model="m")
    assert [h["chunk_id"] for h in out] == ["c2", "c1", "c0"]


def test_the_deadline_keeps_unscored_hits_in_rrf_order(monkeypatch, settings):
    settings.RERANK_WORKERS = 3
    scorer = _Scorer({"c0": 0.1, "c1": 0.2, "c2": 0.3}, sleep={"c0": 2.0, "c1": 2.0})
    monkeypatch.setattr(rerank, "get_client", lambda: scorer)
    started = time.monotonic()
    out = rerank.rerank("q", _hits(3), top_k=3, model="m", deadline_s=0.5)
    assert time.monotonic() - started < 1.5
    assert [h["chunk_id"] for h in out] == ["c2", "c0", "c1"]


def test_a_missing_model_is_checked_once_and_returns_rrf_order(monkeypatch):
    checks = []

    class Missing:
        def has_model(self, model):
            checks.append(model)
            return False

        def score_yes_no(self, *a, **k):
            pytest.fail("scored without a model")

    monkeypatch.setattr(rerank, "get_client", lambda: Missing())
    for _ in range(3):
        out = rerank.rerank("q", _hits(4), top_k=2, model="m")
        assert [h["chunk_id"] for h in out] == ["c0", "c1"]
    assert checks == ["m"]


def test_long_documents_are_truncated(monkeypatch):
    scorer = _Scorer({"c0": 0.5})
    monkeypatch.setattr(rerank, "get_client", lambda: scorer)
    hits = _hits(1)
    hits[0]["text"] = "passage 0<" + "word " * 5000
    scorer.scores = {"c0": 0.5}
    rerank.rerank("q", hits, top_k=1, model="m")
    document = scorer.prompts[0].split("<Document>: ")[1]
    assert chunking.count_tokens(document) < rerank.RERANK_DOC_TOKENS + 40


# ---- hybrid_search integration ------------------------------------------------

@pytest.mark.django_db
def test_hybrid_search_falls_back_to_rrf_when_rerank_raises(monkeypatch):
    from rag import search

    rows = _hits(5)
    monkeypatch.setattr(search, "_fetch", lambda query, params, vector: [dict(r) for r in rows])
    monkeypatch.setattr(search, "get_client", lambda: type("E", (), {"embed": lambda self, t: [[0.1] * 1024]})())

    def boom(*a, **k):
        raise RuntimeError("reranker exploded")

    monkeypatch.setattr("rag.rerank.rerank", boom)
    hits = search.hybrid_search("q", top_k=2, doc_ids=["x"], rerank=True, candidates=5)
    assert [h["chunk_id"] for h in hits] == ["c0", "c1"]


@pytest.mark.django_db
def test_hybrid_search_fetches_candidates_and_reranks_to_top_k(monkeypatch):
    from rag import search

    seen = {}

    def fetch(query, params, vector):
        seen["limit"] = params["top_k"]
        return _hits(params["top_k"])

    monkeypatch.setattr(search, "_fetch", fetch)
    monkeypatch.setattr(search, "get_client", lambda: type("E", (), {"embed": lambda self, t: [[0.1] * 1024]})())
    monkeypatch.setattr("rag.rerank.rerank", lambda q, hits, top_k, **k: list(reversed(hits))[:top_k])
    hits = search.hybrid_search("q", top_k=3, doc_ids=["x"], rerank=True, candidates=12)
    assert seen["limit"] == 12
    assert [h["chunk_id"] for h in hits] == ["c11", "c10", "c9"]

    hits = search.hybrid_search("q", top_k=3, doc_ids=["x"], rerank=False, candidates=12)
    assert seen["limit"] == 3


@pytest.mark.django_db
def test_search_endpoint_passes_rerank_and_candidates(auth_client, monkeypatch):
    seen = {}

    def fake(query, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr("rag.views.hybrid_search", fake)
    r = auth_client.post("/api/search/", {"query": "rent", "rerank": False, "candidates": 12},
                         content_type="application/json")
    assert r.status_code == 200
    assert seen["rerank"] is False and seen["candidates"] == 12

    r = auth_client.post("/api/search/", {"query": "rent"}, content_type="application/json")
    assert seen["rerank"] is None and seen["candidates"] is None

    r = auth_client.post("/api/search/", {"query": "rent", "rerank": "yes"}, content_type="application/json")
    assert r.status_code == 400
