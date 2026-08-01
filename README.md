# OCR + Agentic RAG (Arabic + English)

Upload a document → run several OCR engines → compare them side by side → pick
the best → edit it yourself or have AI correct it → get standardized metadata →
chat with it.

Fully dockerized, no login. Ollama runs on the **host** (Metal GPU); everything
else is a container.

---

## Status at a glance

| Layer | State |
|---|---|
| Infra (compose, Dockerfiles, Postgres+pgvector, Redis, Makefile) | ✅ built & verified |
| Backend (6 Django apps, 40+ endpoints, migrations applied) | ✅ built & verified end to end |
| OCR engine plugin architecture + 8 engines registered | ✅ built, 4 verified live |
| Metadata extraction (plan + extract + retry ladder) | ✅ built & verified live |
| RAG (chunking, pgvector, hybrid RRF) | ✅ verified with Arabic + English retrieval |
| LangGraph agent + SSE chat | ✅ verified live with tool call + explicit citation |
| Frontend (RTL-first) | ✅ production build and type-check verified |
| Tests + `smoke.sh` | ✅ 10 focused tests + full live smoke test passing |

**The core Phase 1 pipeline is now proven end to end.** Remaining work is
browser-level SSE/UI validation and the explicitly deferred UI/sidecar features
listed below.

---

## ✅ Verified working (I ran these and saw the output)

### Environment facts, confirmed not assumed
- `qwen3.5:9b` reports capabilities **`vision`, `tools`, `thinking`** (via `ollama show`).
  This is load-bearing: AI correction sends the *page image* alongside the text,
  and the same model drives the agent. No extra model pulls needed.
- `bge-m3` returns **1024 dims, already L2-normalized**.
- **Cross-lingual retrieval works**: cosine between `عقد إيجار سنوي` and
  `annual lease contract` = **0.83**. This is the entire justification for
  replacing `nomic-embed-text` (English-centric) with `bge-m3`.

### Surya OCR 2 — protocol reverse-engineered
`melashri/surya-ocr-2:q4_k_m` (609 MB) is a **two-stage** model. Full page →
layout JSON; cropped block → text. A full page returns layout *whatever* prompt
you send, so single-call usage silently returns no text.

```
Stage 1: full page, any prompt  → [{"label":"Section-Header","bbox":"316 97 654 162","count":30}, ...]
                                   bbox normalized 0..1000 on BOTH axes
Stage 2: crop by bbox, prompt "ocr_with_boxes" → HTML fragment with that block's text
```

Measured on a bilingual test page:
- **English: perfect** — "…Al-Noor Trading Ltd and Ahmed Al-Rashid, for the total sum of SAR 12,500 per year, effective 2026-01-15."
- **Tables: recovered as HTML** with correct values in both digit systems.
- **Arabic: good but imperfect** — `الحدودة` for `المحدودة`, `المذكوريين` for `المذكورين`.
  Exactly the errors the comparison grid and guarded AI correction exist to catch.

Consequence: `supports_boxes=True` (block-level), and Surya needed its **own
two-stage adapter**, not a generic VLM registry entry. Implemented in
[surya.py](backend/ocr/engines/surya.py).

### Metadata pipeline — full round trip
Both LLM calls run and the result passes Pydantic validation:

```
doc_type        : contract          ← coerced from the model's free-text "Lease Agreement"
dates           : {"document_date": "2026-01-15", ...}
                                    ← Hijri ١٥ رجب ١٤٤٧ correctly converted to Gregorian
identifiers     : {"ref_numbers": ["REF/2026/AR-0417"],
                   "amounts": ["SAR 12,500", "١٢٬٥٠٠ ر.س"]}   ← both digit systems
custom_fields   : annual_rent_amount = 12500.0   ← typed numerically, closed schema
                  date_hijri         = '١٥ رجب ١٤٤٧ هـ'
```

### A real Ollama bug found and worked around
`maxLength > ~1000` in a JSON schema makes Ollama fail with
`"failed to parse grammar"` — it expands into a GBNF character-repetition rule
that blows up. Bisected the threshold (1000 OK, 2000 fails). Fix: length caps are
stripped from the schema sent to the model and enforced by Pydantic on the way
back, where they truncate instead of failing. See `GRAMMAR_SAFE_MAX_LENGTH` in
[schemas.py](backend/enrichment/schemas.py).

### Database
All migrations applied against `pgvector/pgvector:pg17`. Verified in psql:

```
ar_normalize_v1: الأحمد→الاحمد  مَكْتَبَة→مكتبه  ١٢٥٠٠→12500  على→علي
indexes:         chunk_hnsw, chunk_tsv, chunk_trgm, chunk_meta
generated cols:  text_norm, tsv
```

The SQL normalizer output is **character-identical** to the Python mirror in
[arabic.py](backend/common/arabic.py) — the drift risk I was most worried about
is closed. The `U&'…'` escape trap (Postgres regex does *not* read `\uXXXX` in
ordinary string literals, which would silently produce a no-op normalizer) is
avoided.

### Django
Boots clean, **8 engines register**:
`easyocr, native_pdf, surya, tesseract, vlm_deepseek_ocr, vlm_qari, vlm_qwen35, vlm_qwen3vl`

### Arabic utilities
Unit-tested by hand: normalization (6/6 cases), language detection, bidi-suspect
detection on visually-ordered text, gibberish scoring (0.0 clean → 1.0 garbage).
Confirmed `مَكْتَبَة` is **5 graphemes but 9 codepoints** — which is precisely why
every diff in this system goes through `\X` rather than naive `difflib`.

---

## ✅ End-to-end wiring verified

The live smoke flow now proves:

- upload and real-PDF preprocessing
- a mixed Tesseract + Qwen vision OCR chord across the CPU and serialized LLM queues
- comparison, baseline selection, revision creation and finalization
- extraction planning, structured metadata extraction, chunking and `bge-m3` indexing
- Arabic and English hybrid retrieval of the same passage
- LangGraph tool calling, chat token streaming, citation resolution and transcript persistence
- the complete Next.js production build and TypeScript check

The full proof is repeatable with `make smoke`.

## ⚠️ Still needs browser/final-path validation

| Component | File | Remaining check |
|---|---|---|
| Document lifecycle SSE | [documents/sse_views.py](backend/documents/sse_views.py) | HTTP snapshot, replay and event IDs pass; verify automatic reconnect in a browser |
| AI correction task | [correction/tasks.py](backend/correction/tasks.py) | multimodal proposals and guarded rejection pass; verify applying an accepted proposal |
| Frontend interactions | `frontend/src/**` | browser QA for upload, comparison, editor and chat |

---

## ❌ Not built

1. **EasyOCR / PaddleOCR sidecars** — `docker/ocr-easyocr/` and `docker/ocr-paddle/`
   are empty; the compose services and the client adapters exist but the
   `server.py` files do not. Both are Phase 2/3 anyway.
2. **Page image viewer with bbox overlay** — Tesseract returns the boxes and the
   API serves them; no component renders them.
3. **Diff view** — `grapheme_opcodes` and the API endpoint exist; no UI.
4. **Broad test coverage** — the focused regression suite covers the critical
   wiring and correction guards, but most serializers, FSM transitions and
   failure/retry paths still need dedicated tests.

---

## Verification commands

```bash
make up && make ps
make fixtures
make test      # focused fast regressions
make smoke     # full live flow; uses Ollama and takes about 1–2 minutes
```

Then, in priority order:
1. Browser-QA document SSE reconnection and every existing frontend screen.
2. Exercise the multimodal AI-correction task end to end and extend its tests.
3. Build the page-image bbox overlay and grapheme-aware diff UI.
4. Expand serializer, FSM, retry and failure-path coverage.
5. Add the optional EasyOCR and PaddleOCR sidecars in Phase 2/3.

---

## Architecture, briefly

```
Browser ──SSE──> Django (ASGI/uvicorn) ──> Postgres 17 + pgvector
   │                  │                        └─ documents, chunks, langgraph checkpoints
   │                  ├──> Redis ──> Celery (queues: ocr_cpu, llm=1, index)
   │                  └──> host.docker.internal:11434 ──> Ollama (Metal GPU)
   └──> Next.js 15                                          qwen3.5:9b, bge-m3, surya-ocr-2
```

**Key decisions and why**

- **Ollama on the host.** Docker on macOS has no GPU passthrough. In a container
  qwen3.5 runs ~3-6 tok/s and a VLM OCR page takes minutes. A `bundled-ollama`
  compose profile exists for portability.
- **`llm` queue has concurrency 1.** VLM OCR, correction, enrichment and
  embedding all share one Ollama; serializing them stops them fighting over 32 GB.
- **`run_ocr_engine` never raises.** It runs inside a Celery chord — if a member
  raises, the callback never fires and the batch hangs forever. Every failure is
  caught and written to the run row instead.
- **Raw OCR is immutable.** Every edit creates a new `TextRevision` with a parent
  pointer, so you can always get back to what the engine actually produced.
- **`simple` text-search config + `ar_normalize_v1`, not the `arabic` snowball
  stemmer.** Documents are bilingual within a single page and a tsvector has one
  dictionary chain per token type, so `arabic` would mangle the English.
  Orthographic folding fixes the real recall killers (tashkeel, أإآ→ا, ة→ه, ى→ي,
  Arabic-Indic digits); the stemmer fixes none of them.
- **`\f` is the page separator everywhere** — chunker, editor, diff, citations.
- **Never binarize for neural/VLM engines**, and **never apply morphological
  opening to Arabic** — i'jam dots are 1-3 px at 300 dpi, and erasing them
  silently turns ب into ت. See [images.py](backend/ocr/preprocess/images.py).

### Anti-hallucination (five layers)
The AI "improve" feature is the easiest way to quietly ruin this product, so:
1. **Multimodal** — the page image goes with the text; the model can check pixels.
2. **One page at a time** — long context is what makes an LLM start rewriting.
3. **Span replacements only** — the output grammar physically cannot emit a new
   paragraph.
4. **Mechanical guards** — `span_mismatch`, `consensus_locked`, `expansion`,
   `too_dissimilar`, `script_switch`, **`digit_invented`** (a wrong amount on an
   invoice is the most expensive possible failure), `overlap`, `page_rewrite`.
5. **Never auto-applied** — the user accepts each change; rejected proposals are
   shown *with the rule that killed them*, so the guards are auditable.

---

## Requirements

- Docker Desktop, ~8 GB free
- [Ollama](https://ollama.com) on the host
- ~10 GB for models: `qwen3.5:9b` (6.6 GB), `bge-m3` (1.2 GB), `surya-ocr-2` (609 MB)

```bash
cp .env.example .env
make warmup    # pulls the host models
make up
```

`make help` lists everything else.
