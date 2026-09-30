# RAG retrieval quality plan: per-chunk metadata, contextual headers, weighted lexical search, reranking

Status: **in progress**. This document is the design and the implementation plan for
improving retrieval relevance as the library grows to ~800 bilingual (Arabic/English)
OCR'd PDFs. Each step below has a checkbox that is ticked when it lands.

## 1. Why

**The goal.** The data stored in each chunk must be good enough that retrieval
returns the passage that actually answers the question. Observed failures today:

- The wrong chunks are retrieved (a passage from the wrong document or the wrong
  section outranks the right one).
- Cross-document questions ("which documents mention X?") are answered badly
  because the agent's `list_documents` tool only matches titles by substring.

**What a chunk carries today.** Only its raw OCR text, plus a copy of the
document-level metadata in the `meta` JSONB column (used only for a `doc_type`
filter). The embedding sees `section_path + text`. Lexical search sees `text`
alone. Nothing per-chunk reaches the LLM except `section_path`. Every re-index
deletes and recreates all chunks with new ids.

**Scale.** ~800 documents is roughly 50k-150k chunks of ~512 tokens. The LLM
(`qwen3.5:9b` on host Ollama) runs at a few tokens per second on one serialized
worker, so anything that needs one LLM call per chunk takes weeks. The hardware
will improve later, so every expensive step is a setting, cached, and runs in the
background.

## 2. What the evidence says

Surveyed before designing (links are the primary sources):

| Technique | Evidence | Cost for us | Verdict |
|---|---|---|---|
| **Rerank** the fused top candidates with a multilingual cross-encoder | Largest single-step gain in three independent evaluations: [Anthropic contextual retrieval](https://www.anthropic.com/news/contextual-retrieval) (top-20 failure rate 5.7% → 1.9% with rerank), an [Arabic RAG component study](https://arxiv.org/html/2506.06339) (+3.2 avg, +6.0 on ARCD with bge-reranker-v2-m3) | ~2-4 s per search on the current Mac, no re-index | **Do it** |
| **Document + section header** prepended to the chunk before embedding | [dsRAG](https://github.com/D-Star-AI/dsRAG) KITE benchmark 4.72 → 6.04 (→ 8.42 with segment merging); [Snowflake finance RAG](https://www.snowflake.com/en/engineering-blog/impact-retrieval-chunking-finance-rag/) +15-20 pts from *global* document metadata | One LLM call per **section**, not per chunk; document title/summary already exist | **Do it** |
| **Per-chunk keywords** in a *weighted* full-text column | Standard Postgres practice (`setweight` A/B/C/D); [DAPR](https://arxiv.org/pdf/2305.13915) shows entity-heavy prefixes can *hurt* embeddings, so keywords go to lexical search, not the vector | Milliseconds per chunk with [YAKE](https://github.com/INESCTEC/yake) (statistical, has Arabic stopwords) | **Do it** |
| **Per-chunk LLM summaries** (Anthropic-style situating) | -35% failure in Anthropic's eval; a measured *loss* of 0.4-5.8 pts in Snowflake's | One LLM call per chunk: weeks on current hardware | **Later, as a cached background pass on windows of chunks** |
| Hypothetical questions per chunk | Big headline numbers vs weak baselines; most of the value is captured by a reranker | Most output tokens per chunk of any option | Skip |
| [RAPTOR](https://arxiv.org/abs/2401.18059) recursive summaries | +20 pts on thematic questions | Doubles the number of LLM summaries | Skip for now |
| Sentence-aware chunking, page-accurate boundaries | Arabic study: sentence-aware 74.8 vs recursive 69.1 | Free (bug fixes in the chunker) | **Do it** |

[bge-m3](https://huggingface.co/BAAI/bge-m3) needs no instruction prefix; keep
prepended context short and topical.

## 3. Design

### 3.1 Per-chunk metadata

Each chunk keeps the inherited document keys at the top level of `meta` (as
today) and gains its own namespace:

```json
{
  "doc_type": "contract", "title": "...", "keywords": ["..."], "...": "inherited document keys",
  "chunk": {
    "keywords": ["إيجار سنوي", "annual rent", "..."],
    "summary": "One or two sentences about the section this chunk is in (stage 2).",
    "context_model": "qwen3.5:4b",
    "context_version": 1,
    "text_sha256": "..."
  }
}
```

Three real columns feed lexical search and are rebuilt whenever the metadata
changes:

| Column | Content | Lexical weight |
|---|---|---|
| `keywords_text` | space-joined chunk keywords | **A** (highest) |
| `context_text` | section path + section summary | **B** |
| `text` | the raw OCR text (unchanged, used for citations and quotes) | **C** |
| `title_text` | document title | **D** (lowest, so one document's title cannot flood the candidate list) |

### 3.2 Pipeline (two stages)

```
upload → OCR → finalize text → enrich (document metadata, llm queue)
      → INDEX stage 1 (index queue, seconds)          → READY (searchable now)
             chunk (fixed boundaries) → YAKE keywords → header + text → bge-m3
             → weighted tsv (generated column) → document vector
      → CONTEXTUALIZE stage 2 (llm_bg queue, background, cached)
             windows of ~6 chunks → one LLM call per window → section summary + keywords
             → re-embed with the richer header → update chunks IN PLACE (ids kept)
```

Stage 2 never blocks READY, never changes the document state machine, publishes
`context_progress` events, and is cached by content hash so a re-index costs no
LLM calls.

### 3.3 The embedded string

```
Document: {title}. {summary_short[:200]}
Section: {section_path}. {section_summary}

{chunk text}
```

The header is capped at ~80 tokens and is prepended **at embedding time only**;
`text` stays raw.

### 3.4 Retrieval

1. Hybrid search as today (pgvector cosine + weighted tsvector, RRF fusion)
   returns `RERANK_CANDIDATES` fused rows (default 20).
2. A Qwen3-Reranker served by Ollama scores each (query, chunk) pair from the
   next-token probabilities of "yes" vs "no" and the top `top_k` are returned.
3. On any reranker error or deadline, the RRF order is returned unchanged.
4. Tool output (`format_hits`) now carries `section`, `context` and `keywords`
   so the agent sees the chunk's own metadata.

Reranker prompt (sent with `raw: true`, `num_predict: 1`, `logprobs: true`):

```
<|im_start|>system
Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>
<|im_start|>user
<Instruct>: Given a search query, judge whether the document passage answers or is directly relevant to it.
<Query>: {query}
<Document>: {header + text, truncated to ~400 tokens}<|im_end|>
<|im_start|>assistant
<think>

</think>

```

Score: `p_yes = Σ exp(logprob)` over top-logprob tokens equal to "yes" (any case
or leading space), likewise `p_no`; `score = p_yes / (p_yes + p_no)`, `0.0` if
neither is present.

### 3.5 Cross-document questions

A `DocumentVector` row per document embeds `title + summary + keywords + topics`.
`list_documents(query=...)` ranks documents by cosine over it, so "which
documents are about X" works semantically and across languages.

### 3.6 Weighted tsvector (migration 0003)

```sql
ALTER TABLE rag_chunk DROP COLUMN IF EXISTS tsv;
ALTER TABLE rag_chunk ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
  setweight(to_tsvector('simple', ar_normalize_v1(coalesce(keywords_text, ''))), 'A') ||
  setweight(to_tsvector('simple', ar_normalize_v1(coalesce(context_text, ''))), 'B') ||
  setweight(to_tsvector('simple', ar_normalize_v1(coalesce(text, ''))), 'C') ||
  setweight(to_tsvector('simple', ar_normalize_v1(coalesce(title_text, ''))), 'D')
) STORED;
CREATE INDEX IF NOT EXISTS chunk_tsv ON rag_chunk USING gin (tsv);
```

`ar_normalize_v1` is STRICT, so every branch is wrapped in `coalesce(..., '')`
*inside* the call; otherwise one NULL column makes the whole vector NULL.

## 4. Settings

| Setting | Default | Meaning |
|---|---|---|
| `RERANK_ENABLED` | `1` | Rerank hybrid candidates (off in the test settings) |
| `RERANK_MODEL` | `dengcao/Qwen3-Reranker-0.6B` | Ollama model used for scoring |
| `RERANK_CANDIDATES` | `20` | Fused rows fetched before reranking (deep research uses 12) |
| `RERANK_WORKERS` | `4` | Parallel scoring threads (only helps if the host runs `OLLAMA_NUM_PARALLEL>=4`) |
| `RERANK_TIMEOUT_S` | `8` | Overall deadline per search; unscored candidates keep RRF order |
| `RERANK_CALL_TIMEOUT_S` | `5` | Per-pair timeout |
| `CHUNK_CONTEXT_ENABLED` | `1` | Run stage 2 after indexing (off in the test settings) |
| `CHUNK_CONTEXT_MODEL` | `qwen3.5:4b` | Stage-2 model; falls back to `LLM_MODEL` if not pulled |
| `CHUNK_CONTEXT_WINDOW_TOKENS` | `3000` | Max tokens per stage-2 window |
| `CHUNK_CONTEXT_WINDOW_CHUNKS` | `6` | Max chunks per stage-2 window |
| `CHUNK_CONTEXT_PROMPT_VERSION` | `v1` | Part of the cache key; bump to regenerate |

## 5. Measuring it

`python manage.py rag_eval --file queries.jsonl --user <name> [--compare]`
reads lines of

```json
{"query": "ما هي مدة العقد؟", "expected_document_id": "…", "expected_pages": [3]}
```

and prints document recall@5/@10, page recall@5/@10, MRR@10 and mean latency;
`--compare` runs with and without the reranker side by side. Write 30-50 real
queries **before** the retrieval changes land and keep the baseline JSON.

## 6. Implementation checklist

- [x] **0a.** This document, linked from the README.
- [x] **0.** `rag_eval` management command + metric tests + Makefile target; capture the baseline.
- [x] **1.** Chunker fixes: overlap never crosses a page, `page_start` correct, section heading persists across pages, tighter heading detection, tail chunk carries section/meta. Tests in `tests/unit/test_chunking.py`.
- [x] **2.** `rag/context.py` (header builder) and `rag/keywords.py` (YAKE, ar/en/mixed) + `yake` dependency.
- [x] **3.** Schema (`keywords_text`, `context_text`, `title_text`, `ChunkContext`, `DocumentVector`, `IndexRun.context_*`), migration 0003 with the weighted `tsv`, stage-1 indexing writes the new fields, `format_hits` exposes them, `reindex_all` command.
- [x] **4.** Reranker: `OllamaClient.score_yes_no`, `rag/rerank.py`, `hybrid_search(rerank=, candidates=)`, settings, warmup script.
- [x] **5.** `DocumentVector` upsert during indexing; semantic `list_documents`.
- [x] **6.** Stage 2: `rag/contextualize.py`, `contextualize_document` task on the `llm_bg` queue, cache, in-place updates, progress events, compose/route changes.
- [x] **7.** `.env.example`, README, smoke test updates. **Pending (needs the user's query set):** run the eval before/after and record the numbers here.

## 7. Operating notes

- New documents are searchable as soon as stage 1 finishes; stage 2 improves
  them later. Existing documents need `make reindex-all` once after deploying.
- The reranker model is pulled by `make warmup`. If it is missing, search logs
  one warning and falls back to RRF order.
- The `worker-llm` container consumes `llm` before `llm_bg`, so enrichment of a
  new upload is never queued behind background contextualization.
- Stage-2 results are cached in `ChunkContext` by (title + section + window text,
  model, prompt version); a re-index of an unchanged document makes zero LLM calls.
- DOCX is **not** an ingestion format (the upload validator accepts PDF and
  images). Adding it is a separate task.
