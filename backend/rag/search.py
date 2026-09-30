"""Hybrid retrieval: dense vectors + lexical, fused with RRF.

Vector is weighted above lexical because cross-lingual retrieval — an Arabic
query surfacing an English passage — is a real requirement here and the lexical
channel cannot do it at all. bge-m3 can: measured cosine between
"عقد إيجار سنوي" and "annual lease contract" is 0.83.

If both channels come back empty a trigram fallback runs, which is what rescues
queries whose spelling doesn't quite match the OCR's.
"""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.db import connection, transaction

from common.ollama import get_client
from rag.contextualize import merge_keywords

logger = logging.getLogger(__name__)

RRF_K = 60
VECTOR_WEIGHT = 1.2
LEXICAL_WEIGHT = 1.0
CANDIDATES = 80

HYBRID_SQL = """
WITH params AS (
  SELECT %(qvec)s::vector AS qv,
         websearch_to_tsquery('simple', ar_normalize_v1(%(qtext)s)) AS qq
),
vec AS (
  SELECT c.id, ROW_NUMBER() OVER (ORDER BY c.embedding <=> p.qv) AS rnk
  FROM rag_chunk c, params p
  WHERE c.embedding IS NOT NULL
    AND (%(doc_ids)s::uuid[] IS NULL OR c.document_id = ANY(%(doc_ids)s::uuid[]))
    AND (%(doc_type)s::text IS NULL OR c.meta->>'doc_type' = %(doc_type)s)
    AND (%(lang)s::text IS NULL OR c.lang = %(lang)s)
  ORDER BY c.embedding <=> p.qv
  LIMIT %(candidates)s
),
fts AS (
  SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.tsv, p.qq) DESC) AS rnk
  FROM rag_chunk c, params p
  WHERE c.tsv @@ p.qq
    AND (%(doc_ids)s::uuid[] IS NULL OR c.document_id = ANY(%(doc_ids)s::uuid[]))
    AND (%(doc_type)s::text IS NULL OR c.meta->>'doc_type' = %(doc_type)s)
    AND (%(lang)s::text IS NULL OR c.lang = %(lang)s)
  ORDER BY ts_rank_cd(c.tsv, p.qq) DESC
  LIMIT %(candidates)s
),
fused AS (
  SELECT COALESCE(v.id, f.id) AS id,
         COALESCE(%(wv)s / (%(k)s + v.rnk), 0)
       + COALESCE(%(wl)s / (%(k)s + f.rnk), 0) AS rrf,
         v.rnk AS rank_vec,
         f.rnk AS rank_fts
  FROM vec v FULL OUTER JOIN fts f ON v.id = f.id
)
SELECT c.id, c.document_id, d.title, d.original_filename,
       c.page_start, c.page_end, c.text, c.section_path, c.lang, c.meta,
       c.keywords_text, c.context_text,
       fu.rrf, fu.rank_vec, fu.rank_fts
FROM fused fu
JOIN rag_chunk c ON c.id = fu.id
JOIN documents_document d ON d.id = c.document_id
ORDER BY fu.rrf DESC
LIMIT %(top_k)s;
"""

# HYBRID_SQL's full-text half on its own, for when no query vector exists.
LEXICAL_SQL = """
WITH params AS (
  SELECT websearch_to_tsquery('simple', ar_normalize_v1(%(qtext)s)) AS qq
),
fts AS (
  SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.tsv, p.qq) DESC) AS rnk
  FROM rag_chunk c, params p
  WHERE c.tsv @@ p.qq
    AND (%(doc_ids)s::uuid[] IS NULL OR c.document_id = ANY(%(doc_ids)s::uuid[]))
    AND (%(doc_type)s::text IS NULL OR c.meta->>'doc_type' = %(doc_type)s)
    AND (%(lang)s::text IS NULL OR c.lang = %(lang)s)
  ORDER BY ts_rank_cd(c.tsv, p.qq) DESC
  LIMIT %(top_k)s
)
SELECT c.id, c.document_id, d.title, d.original_filename,
       c.page_start, c.page_end, c.text, c.section_path, c.lang, c.meta,
       c.keywords_text, c.context_text,
       %(wl)s / (%(k)s + f.rnk) AS rrf, NULL::bigint AS rank_vec, f.rnk AS rank_fts
FROM fts f
JOIN rag_chunk c ON c.id = f.id
JOIN documents_document d ON d.id = c.document_id
ORDER BY f.rnk;
"""

TRIGRAM_FALLBACK_SQL = """
SELECT c.id, c.document_id, d.title, d.original_filename,
       c.page_start, c.page_end, c.text, c.section_path, c.lang, c.meta,
       c.keywords_text, c.context_text,
       similarity(c.text_norm, ar_normalize_v1(%(qtext)s)) AS rrf,
       NULL::bigint AS rank_vec, NULL::bigint AS rank_fts
FROM rag_chunk c
JOIN documents_document d ON d.id = c.document_id
WHERE similarity(c.text_norm, ar_normalize_v1(%(qtext)s)) > 0.25
  AND (%(doc_ids)s::uuid[] IS NULL OR c.document_id = ANY(%(doc_ids)s::uuid[]))
ORDER BY rrf DESC
LIMIT %(top_k)s;
"""

COLUMNS = [
    "chunk_id", "document_id", "document_title", "original_filename",
    "page_start", "page_end", "text", "section_path", "lang", "meta",
    "keywords_text", "context_text",
    "score", "rank_vec", "rank_fts",
]


def hybrid_search(
    query: str,
    *,
    top_k: int = 8,
    doc_ids: list[str] | None = None,
    doc_type: str | None = None,
    lang: str | None = None,
    rerank: bool | None = None,
    candidates: int | None = None,
) -> list[dict]:
    """Top `top_k` chunks for `query`.

    With reranking (default: settings.RERANK_ENABLED), `candidates` fused rows
    (default: settings.RERANK_CANDIDATES) are fetched and reordered by the
    reranker; any reranker failure returns the RRF order instead. `rrf`,
    `rank_vec` and `rank_fts` are always kept on each hit; `score` is the
    reranker probability when reranked, otherwise the fused score.
    """
    if not query or not query.strip():
        return []
    # `None` means "no restriction"; an empty list means "nothing is in scope"
    # (e.g. a user with no documents) and must never widen to the whole corpus.
    if doc_ids is not None and not doc_ids:
        return []

    try:
        vector = get_client().embed([query])[0]
    except Exception:
        logger.warning("query embedding failed; lexical only", exc_info=True)
        vector = None

    if rerank is None:
        rerank = settings.RERANK_ENABLED
    if rerank:
        limit = max(top_k, candidates or settings.RERANK_CANDIDATES)
    else:
        limit = top_k

    params = {
        "qtext": query,
        "qvec": _vector_literal(vector) if vector else None,
        "doc_ids": list(doc_ids) if doc_ids is not None else None,
        "doc_type": doc_type,
        "lang": lang,
        "top_k": limit,
        "candidates": CANDIDATES,
        "k": float(RRF_K),
        "wv": VECTOR_WEIGHT,
        "wl": LEXICAL_WEIGHT,
    }

    hits = _fetch(query, params, vector)
    for hit in hits:
        hit["rrf"] = hit["score"]
    if not rerank or len(hits) <= 1:
        return hits[:top_k]
    try:
        from rag import rerank as reranker

        return reranker.rerank(query, hits, top_k=top_k)
    except Exception:
        logger.warning("reranking failed; returning the RRF order", exc_info=True)
        return hits[:top_k]


def _fetch(query: str, params: dict, vector: list[float] | None) -> list[dict]:
    # SET LOCAL lasts until the end of the transaction; under autocommit it
    # would end with the SET itself and have no effect.
    with transaction.atomic(), connection.cursor() as cursor:
        # Raising ef_search improves recall at negligible cost on this corpus.
        cursor.execute("SET LOCAL hnsw.ef_search = 100")

        # Without a query vector (Ollama down), the lexical half still runs.
        cursor.execute(HYBRID_SQL if vector is not None else LEXICAL_SQL, params)
        rows = cursor.fetchall()

        if not rows:
            # Nothing matched semantically or lexically — try fuzzy. This is
            # what saves a query whose spelling differs from the OCR's.
            cursor.execute(TRIGRAM_FALLBACK_SQL, params)
            rows = cursor.fetchall()

    return [_row(row) for row in rows]


def _row(row) -> dict:
    hit = dict(zip(COLUMNS, row))
    # Django's psycopg setup returns jsonb from a raw cursor as text.
    if isinstance(hit["meta"], str):
        try:
            hit["meta"] = json.loads(hit["meta"])
        except ValueError:
            hit["meta"] = {}
    return hit


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"


def format_hits(hits: list[dict], max_chars: int = 1200) -> list[dict]:
    """Shape hits for the agent's tool output — compact, with citation handles.

    `context` is the stage-2 section summary (empty until it has run) and
    `keywords` the chunk's own keywords, so the agent can tell passages apart
    without reading every one in full.
    """
    return [
        {
            "chunk_id": str(hit["chunk_id"]),
            "document_id": str(hit["document_id"]),
            "document_title": hit["document_title"] or hit["original_filename"],
            "page_start": hit["page_start"],
            "page_end": hit["page_end"],
            "section": hit["section_path"],
            "context": _chunk_meta(hit).get("summary") or "",
            "keywords": merge_keywords(
                _chunk_meta(hit).get("keywords") or [], _chunk_meta(hit).get("context_keywords") or []
            ),
            "text": hit["text"][:max_chars],
            "score": round(float(hit["score"] or 0), 5),
        }
        for hit in hits
    ]


def _chunk_meta(hit: dict) -> dict:
    meta = hit.get("meta") or {}
    chunk = meta.get("chunk") if isinstance(meta, dict) else None
    return chunk if isinstance(chunk, dict) else {}
