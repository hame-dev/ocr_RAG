"""Rerank fused hybrid candidates with a Qwen3-Reranker served by Ollama.

Reranking the top ~20 fused candidates is the largest single retrieval gain in
every evaluation we found, including one on Arabic. The model is a causal LM
used as a judge: it sees (instruction, query, passage) and we read the
probability that its next token is "yes" (OllamaClient.score_yes_no).

Reranking must never make search worse than no reranking: a missing model,
an error or a deadline all fall back to the RRF order, and a hit that could
not be scored is never dropped, only placed after the scored ones.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed

from django.conf import settings

from common.ollama import get_client
from rag.chunking import count_tokens
from rag.context import build_context_header, embed_input

logger = logging.getLogger(__name__)

RERANK_DOC_TOKENS = 400
AVAILABILITY_TTL_S = 300

RERANK_INSTRUCTION = (
    "Given a search query, judge whether the document passage answers or is directly relevant to it."
)

# The model's own chat frame, sent with raw=true so Ollama adds no template.
# The empty think block is how Qwen3 is told to answer without reasoning.
RERANK_PROMPT = (
    "<|im_start|>system\n"
    "Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
    'Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n"
    "<Instruct>: {instruction}\n"
    "<Query>: {query}\n"
    "<Document>: {document}<|im_end|>\n"
    "<|im_start|>assistant\n"
    "<think>\n\n</think>\n\n"
)

# model -> (available, checked_at). A missing model costs one check per TTL,
# not one failed call per candidate per search.
_availability: dict[str, tuple[bool, float]] = {}


def _available(model: str) -> bool:
    cached = _availability.get(model)
    now = time.monotonic()
    if cached and now - cached[1] < AVAILABILITY_TTL_S:
        return cached[0]
    try:
        ok = bool(get_client().has_model(model))
    except Exception:
        logger.warning("could not check for reranker %s", model, exc_info=True)
        ok = False
    if not ok:
        logger.warning("reranker %s is not available; using RRF order (run `make warmup`)", model)
    _availability[model] = (ok, now)
    return ok


def _truncate(text: str, max_tokens: int) -> str:
    tokens = count_tokens(text)
    if tokens <= max_tokens:
        return text
    # Proportional cut, then tighten: one or two tokenizer calls, not one per word.
    cut = text[: max(1, len(text) * max_tokens // tokens)]
    while cut and count_tokens(cut) > max_tokens:
        cut = cut[: int(len(cut) * 0.9)]
    return cut


def document_for(hit: dict) -> str:
    meta = hit.get("meta") if isinstance(hit.get("meta"), dict) else {}
    section_summary = ((meta or {}).get("chunk") or {}).get("summary") or ""
    header = build_context_header(
        hit.get("document_title") or hit.get("original_filename") or "",
        "",
        hit.get("section_path") or "",
        section_summary,
    )
    return _truncate(embed_input(header, hit.get("text") or ""), RERANK_DOC_TOKENS)


def rerank(
    query: str,
    hits: list[dict],
    *,
    top_k: int,
    model: str | None = None,
    deadline_s: float | None = None,
) -> list[dict]:
    """Reorder `hits` (in RRF order) by reranker score and return the top_k.

    Each returned hit keeps its fused score as `rrf`; scored hits get the
    reranker probability as `score` and `rerank_score`.
    """
    for hit in hits:
        hit.setdefault("rrf", hit.get("score"))
    if not hits:
        return []
    model = model or settings.RERANK_MODEL
    if not _available(model):
        return hits[:top_k]

    deadline_s = settings.RERANK_TIMEOUT_S if deadline_s is None else deadline_s
    call_timeout = settings.RERANK_CALL_TIMEOUT_S
    client = get_client()
    started = time.monotonic()
    scores: dict[int, float] = {}

    def score(position: int) -> tuple[int, float]:
        prompt = RERANK_PROMPT.format(
            instruction=RERANK_INSTRUCTION, query=query, document=document_for(hits[position])
        )
        return position, client.score_yes_no(model, prompt, timeout=call_timeout)

    executor = ThreadPoolExecutor(max_workers=max(1, settings.RERANK_WORKERS))
    futures = [executor.submit(score, i) for i in range(len(hits))]
    try:
        for future in as_completed(futures, timeout=max(0.0, deadline_s - (time.monotonic() - started))):
            try:
                position, value = future.result()
            except Exception:
                logger.warning("rerank call failed; that hit keeps its RRF place", exc_info=True)
                continue
            scores[position] = value
    except FuturesTimeout:
        logger.warning("rerank deadline (%.1fs) hit: %d/%d scored", deadline_s, len(scores), len(hits))
    finally:
        # Never wait on stragglers: their results are simply not used.
        executor.shutdown(wait=False, cancel_futures=True)

    scored = sorted(scores, key=lambda i: scores[i], reverse=True)
    unscored = [i for i in range(len(hits)) if i not in scores]
    out = []
    for i in scored + unscored:
        hit = hits[i]
        if i in scores:
            hit["rerank_score"] = scores[i]
            hit["score"] = scores[i]
        out.append(hit)
    return out[:top_k]
