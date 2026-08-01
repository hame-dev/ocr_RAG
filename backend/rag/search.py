"""Hybrid retrieval: dense vectors + lexical, fused with RRF.

Vector is weighted above lexical because cross-lingual retrieval — an Arabic
query surfacing an English passage — is a real requirement here and the lexical
channel cannot do it at all. bge-m3 can: measured cosine between
"عقد إيجار سنوي" and "annual lease contract" is 0.83.

If both channels come back empty a trigram fallback runs, which is what rescues
queries whose spelling doesn't quite match the OCR's.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import connection

from common.ollama import get_client

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
       fu.rrf, fu.rank_vec, fu.rank_fts
FROM fused fu
JOIN rag_chunk c ON c.id = fu.id
JOIN documents_document d ON d.id = c.document_id
ORDER BY fu.rrf DESC
LIMIT %(top_k)s;
"""

TRIGRAM_FALLBACK_SQL = """
SELECT c.id, c.document_id, d.title, d.original_filename,
       c.page_start, c.page_end, c.text, c.section_path, c.lang, c.meta,
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
    "score", "rank_vec", "rank_fts",
]


def hybrid_search(
    query: str,
    *,
    top_k: int = 8,
    doc_ids: list[str] | None = None,
    doc_type: str | None = None,
    lang: str | None = None,
) -> list[dict]:
    if not query or not query.strip():
        return []

    try:
        vector = get_client().embed([query])[0]
    except Exception:
        logger.warning("query embedding failed; lexical only", exc_info=True)
        vector = None

    params = {
        "qtext": query,
        "qvec": _vector_literal(vector) if vector else None,
        "doc_ids": list(doc_ids) if doc_ids else None,
        "doc_type": doc_type,
        "lang": lang,
        "top_k": top_k,
        "candidates": CANDIDATES,
        "k": float(RRF_K),
        "wv": VECTOR_WEIGHT,
        "wl": LEXICAL_WEIGHT,
    }

    with connection.cursor() as cursor:
        # Raising ef_search improves recall at negligible cost on this corpus.
        cursor.execute("SET LOCAL hnsw.ef_search = 100")

        if vector is None:
            rows = []
        else:
            cursor.execute(HYBRID_SQL, params)
            rows = cursor.fetchall()

        if not rows:
            # Nothing matched semantically or lexically — try fuzzy. This is
            # what saves a query whose spelling differs from the OCR's.
            cursor.execute(TRIGRAM_FALLBACK_SQL, params)
            rows = cursor.fetchall()

    return [dict(zip(COLUMNS, row)) for row in rows]


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"


def format_hits(hits: list[dict], max_chars: int = 1200) -> list[dict]:
    """Shape hits for the agent's tool output — compact, with citation handles."""
    return [
        {
            "chunk_id": str(hit["chunk_id"]),
            "document_id": str(hit["document_id"]),
            "document_title": hit["document_title"] or hit["original_filename"],
            "page_start": hit["page_start"],
            "page_end": hit["page_end"],
            "section": hit["section_path"],
            "text": hit["text"][:max_chars],
            "score": round(float(hit["score"] or 0), 5),
        }
        for hit in hits
    ]
