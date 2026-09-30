# Review: what to fix and what to improve

Reviewed on 2026-09-30 against branch `v1` (commit `73f0769`). Nothing in the
code was changed; this file is the only output.

## Status (updated 2026-09-30, after the fixes)

Everything in sections 1 and 2 is fixed except the items listed under "Still
open" below. Verification: 160 backend tests pass (20 of them new, in
`backend/tests/unit/test_review_fixes.py` and the seed-user test), the sandbox
suite passes, `tsc --noEmit` and a production `next build` pass, and
`manage.py check --deploy` passes under the new `config.settings.prod`. It was
also checked live: the login throttle holds against forged `X-Forwarded-For`,
`/login?next=/%09/evil.com` lands on `/`, both Celery workers answer their
healthcheck, and existing chats load.

What changed, beyond the one-line fixes in the tables:

- **Chat:** one turn at a time per conversation (Redis lock, 409 when busy).
  Superseded LangGraph checkpoints are pruned after each turn, and agent memory
  is deleted by a `post_delete` signal, so user deletion cleans it up too.
- **OCR:** batches get closed when preprocessing fails, a soft time limit per
  engine run, a beat task that closes batches stuck past `OCR_BATCH_STALE_S`,
  and cancel is honoured by the tasks themselves.
- **Uploads:** limits on page count and pixels (`MAX_UPLOAD_PAGES`,
  `MAX_PAGE_PIXELS`), an early `Content-Length` check, and a cap on
  uncompressed `.docx` size.
- **Deployment:** `config/settings/prod.py`, a `prod` target in the frontend
  Dockerfile (`next build` + `next start`, non-root), `INSTALL_DEV=0` for a
  backend image without test packages (`requirements-dev.txt`), two worker
  services with healthchecks, a root `.dockerignore`, `pytest.ini`, CI in
  `.github/workflows/ci.yml`, `make test-frontend`, and no seeding of the
  published default password outside `DEBUG`.

**Still open** (not done in this pass):

- A production `docker-compose.prod.yml` (no bind mounts, no published
  Postgres port, TLS proxy, non-root backend). The pieces it needs now exist.
- Backups for `pgdata` and `media`.
- ESLint (`npm run lint` still needs `eslint` + `eslint-config-next`) and
  ruff/mypy for the backend.
- The `easyocr` / `paddle` / `all-engines` compose profiles still point at
  sidecars that do not exist.
- Whether `docs/` should be tracked: a decision for you.
- Frontend a11y: `SourcePicker` menu roles, the keyboard behaviour of the
  segmented control, the non-functional preview button in `AttachmentList`, and
  an `aria-live` region for the streamed answer (only the typing indicator got
  a status role).
- Smaller frontend items: attachments uploaded before a failed conversation
  creation wait for the hourly cleanup instead of being deleted at once;
  concurrent source-scope PATCHes can race; GETs send `Content-Type` and so
  trigger a CORS preflight; unit suffixes (s, MB) are Latin in Arabic;
  `humanizeKey` labels are English only; 16 i18n keys are unused.
- `init_checkpointer` failing at boot still makes chat fail instead of running
  without memory.
- Cancelled OCR tasks are not revoked from the broker. They exit immediately
  and never overwrite the cancelled state, but they still take a slot.

## Summary

The project is in good shape. The backend suite (140 tests) and the sandbox
suite (11 tests) pass, and `tsc --noEmit` is clean on the frontend. No per-user
data leak was found: every view, the chat tools, retrieval, attachments and
generated files are filtered by owner. The code-runner isolation, CSRF handling,
Markdown/Mermaid rendering and upload type-sniffing were checked and are sound.

What needs work falls into three groups:

1. A handful of real bugs, two of which affect answer quality (stale search
   chunks, conversation memory being wiped).
2. Two security issues that are easy to fix (login throttle bypass, open
   redirect).
3. The stack as shipped is a development stack. It should not be put on a shared
   server without the changes in [Deployment](#3-deployment-readiness).

**How this was checked.** "Confirmed" means the code path was read end to end,
and where noted, reproduced. "Plausible" means it follows from the code but was
not reproduced. Not reviewed in depth: the individual OCR engine adapters
(`ocr/engines/*`), `enrichment/extractor.py`, `rag/chunking.py`, the correction
guards, and the Python/SQL Arabic normalizer parity.

---

## 1. Fix first

### 1.1 Old text stays searchable after a document is re-indexed

- **Where:** [rag/tasks.py:85](backend/rag/tasks.py:85), [rag/search.py](backend/rag/search.py)
- **Status:** confirmed by reading.
- **Problem:** indexing deletes chunks for the *same revision* only, and the
  search SQL does not filter by the document's current revision. Edit a ready
  document, finalize the new revision and re-index: the chunks of the previous
  revision remain, so chat retrieves and cites text the user has already
  corrected, alongside the new text.
- **Fix:** delete by document (`Chunk.objects.filter(document=document).delete()`)
  inside one transaction with the `bulk_create`. Add a test that indexes two
  revisions and asserts only the latest is returned.

### 1.2 Conversation memory is wiped on the turn after summarization

- **Where:** [chat/sse_views.py:243](backend/chat/sse_views.py:243), [chat/graph.py:253](backend/chat/graph.py:253)
- **Status:** confirmed by reading.
- **Problem:** every turn passes `"summary": ""` into the graph, which overwrites
  the summary that `prepare` saved on the previous turn. Once a chat passes 20
  messages, older messages are summarized and removed, and on the next turn the
  summary is reset to empty. The agent then remembers only the last 8 messages.
- **Fix:** drop `summary` from the per-turn input, and include the existing
  summary in the summarizer prompt so later summaries build on it.
- **Why tests missed it:** `tests/fakes.py` forces the checkpointer to `None`, so
  multi-turn memory is never exercised.

### 1.3 Login throttle can be bypassed with a forged header

- **Where:** [accounts/views.py:35](backend/accounts/views.py:35)
- **Status:** confirmed by running it. Eight attempts from one address got 429
  after the fifth; twenty attempts each with a different `X-Forwarded-For` value
  all went through.
- **Problem:** DRF's `get_ident` trusts `X-Forwarded-For` when `NUM_PROXIES` is
  not set, and the backend port is exposed directly. An attacker gets unlimited
  password guesses against one account.
- **Fix:** set `REST_FRAMEWORK["NUM_PROXIES"]` from the environment (0 when there
  is no proxy), and add a second throttle keyed on username alone.

### 1.4 Open redirect on the login page

- **Where:** [frontend/src/lib/redirect.ts:7](frontend/src/lib/redirect.ts:7)
- **Status:** confirmed. `new URL("/\t/evil.com", "http://localhost:3000")`
  resolves to `http://evil.com/`.
- **Problem:** `safeNext` rejects `//` and `/\` but not a tab or newline after the
  first slash, which the URL parser strips. `/login?next=/%09/evil.com` sends a
  signed-in user to another site.
- **Fix:** resolve with `new URL(value, location.origin)`, require the same
  origin, and return `pathname + search + hash`.

### 1.5 A failed preprocess during OCR leaves the document stuck

- **Where:** [ocr/tasks.py:63](backend/ocr/tasks.py:63), [common/fsm.py:38](backend/common/fsm.py:38)
- **Status:** confirmed by reading.
- **Problem:** `start_ocr_batch` moves the document to `ocr_running` and then
  chains `preprocess_document`. If preprocessing raises there, the handler tries
  `ocr_running → failed`, which is not an allowed transition, so
  `InvalidTransition` is raised inside the handler. The document stays
  `ocr_running`, the batch stays `running` and the runs stay `queued`, because
  the chord never starts.
- **Fix:** in that handler, move to `ocr_failed` when the document is in an OCR
  state, and mark the batch and its queued runs failed.
- **Related:** no Celery task sets `soft_time_limit` / `time_limit`, although
  `run_ocr_engine` has a handler for `SoftTimeLimitExceeded`. A hung engine is
  bounded only by that engine's own HTTP timeout. Add limits, and a periodic
  task that fails batches stuck in `running` past a deadline.

### 1.6 Starting a new chat mid-stream can crash the page

- **Where:** [frontend/src/components/ChatWorkspace.tsx:275](frontend/src/components/ChatWorkspace.tsx:275)
- **Status:** confirmed by reading.
- **Problem:** `reset()` aborts the stream and empties the message list; the abort
  handler then calls `updateLast`, which reads `last.content` on an empty list.
  There is no `error.tsx`, so the whole page goes to the Next error screen.
- **Fix:** make `updateLast` a no-op on an empty list and tag each stream with a
  run id so a discarded stream cannot write state. Add an `error.tsx` boundary.

---

## 2. Should fix

### Backend

| # | Where | Problem | Fix |
|---|-------|---------|-----|
| 2.1 | [documents/views.py](backend/documents/views.py) | Deleting a document leaves `media/docs/<id>/` (original file, rasters, thumbnail) on disk. Chat files have a `post_delete` cleanup; documents have none. | Add a `post_delete` receiver on `Document` that removes the directory. |
| 2.2 | [documents/views.py:233](backend/documents/views.py:233) | `finalize_text` returns 500 when the document is in `ocr_partial`, `ocr_running` or `indexing`: the transition is not allowed and `InvalidTransition` is unhandled. `is_final` has already been saved by then. | Validate the state first and return 409; wrap the writes in `transaction.atomic()`. |
| 2.3 | [enrichment/tasks.py:108](backend/enrichment/tasks.py:108) | Re-running enrichment overwrites metadata a person edited. `human_edited` is set on PATCH but never read, although the comment says it protects those edits. | Skip or merge human-edited fields when `human_edited` is true, or ask before overwriting. |
| 2.4 | [documents/serializers.py:119](backend/documents/serializers.py:119), [preprocess.py](backend/documents/services/preprocess.py) | No page-count or pixel limit. A 50 MB PDF with thousands of pages is rasterized in full, and a small image file with very large dimensions is decoded whole by OpenCV. | Reject above a configurable page count and pixel count at upload. |
| 2.5 | [ocr/tasks.py:41](backend/ocr/tasks.py:41) | `probe_engine_health` inserts one row per engine every 60 s and nothing deletes them (about 14,000 rows a day with ten engines). | Keep the latest row per engine, or prune rows older than a few days. |
| 2.6 | [chat/checkpointer.py](backend/chat/checkpointer.py) | Checkpoint tables are never pruned, and attachment images are stored in them as base64. They are deleted only when the conversation is deleted through the API; deleting a user leaves them behind. | Prune to the latest checkpoint per thread after each turn; move `delete_thread` into a `post_delete` signal on `Conversation`. |
| 2.7 | [chat/attachments.py:139](backend/chat/attachments.py:139) | Text attachments that are not UTF-8 are tried as `utf-16` before `cp1256`, which "succeeds" on most even-length files and produces garbage. Arabic Windows text files are the likely victims. | Try `utf-16` only when the data starts with a BOM. |
| 2.8 | [chat/sse_views.py:98](backend/chat/sse_views.py:98) | Two simultaneous sends to one conversation (double-click, two tabs) run two graph runs on the same thread; one turn is lost from the agent's memory. | Take a short Redis lock per conversation and return 409 if held. |
| 2.9 | [chat/sse_views.py:149](backend/chat/sse_views.py:149) | `Conversation.updated_at` is only written when the title is first set, so the sidebar orders chats by first message, not latest activity. | Save `updated_at` on every turn. |
| 2.10 | [chat/views.py:126](backend/chat/views.py:126) | The conversation list prefetches every message, attachment and file of every listed conversation, then runs one `count()` per row. | Prefetch only for `retrieve`; annotate the count for `list`. |
| 2.11 | [ocr/views.py:43](backend/ocr/views.py:43) | Cancelling a batch only relabels the rows. The Celery tasks keep running, and a running task later overwrites `cancelled` with `succeeded`. | Revoke the tasks, and have `run_ocr_engine` re-check for `cancelled` before saving. |
| 2.12 | [rag/search.py:126](backend/rag/search.py:126) | `SET LOCAL hnsw.ef_search` runs outside a transaction, so it has no effect. When the query embedding fails, the code skips full-text search and goes straight to the trigram fallback, despite logging "lexical only". | Wrap the search in `transaction.atomic()`; run the FTS branch without the vector. |

Smaller items in the same area:

- Malformed request bodies return 500 instead of 400 in `chat_stream` (JSON array
  body, non-string `content`, non-UUID `attachment_ids`), `revision_diff`
  (non-integer `a`/`b`) and `/api/search/` (non-string `query`).
- `list_documents` in [chat/tools.py:125](backend/chat/tools.py:125) filters on
  `status="ready"` while chat scope uses `("ready", "indexed")`.
- The chat tools treat `doc_ids=None` as "whole corpus". Every caller passes a
  list today, so this is not exploitable, but it should default to deny.
- Upload size is checked after Django has spooled the whole body to disk.
- A stream cancelled while suspended at a `yield` may not save the partial answer
  (plausible, not reproduced).

### Frontend

| # | Where | Problem | Fix |
|---|-------|---------|-----|
| 2.13 | [lib/sse.ts:133](frontend/src/lib/sse.ts:133) | If the chat stream ends without a `done` or `error` event (proxy timeout, worker killed), the answer keeps its typing cursor forever and buffered tokens are dropped. | Track whether a terminal event arrived; if not, flush and report an error. |
| 2.14 | [useDocumentStream.ts:42](frontend/src/components/document/useDocumentStream.ts:42) | The document progress stream has no error handler. After a backend restart or an expired session during OCR, the page stays on "running" until reload. | Add `onerror`, resubscribe with backoff, and fall back to polling. |
| 2.15 | [ReviewStep.tsx:93](frontend/src/components/document/ReviewStep.tsx:93) | Unsaved OCR edits are lost without warning when switching step or using the sidebar; only a browser reload is guarded. | Confirm before leaving, or keep the draft in the parent. |
| 2.16 | [ChatWorkspace.tsx:150](frontend/src/components/ChatWorkspace.tsx:150) | Leaving a chat mid-stream and coming back shows the transcript from before the question, until a hard reload. | Remove the cached conversation query when a stream is aborted. |
| 2.17 | [Markdown.tsx:53](frontend/src/components/Markdown.tsx:53) | The `<pre>` unwrap for Mermaid never matches, so every diagram renders inside a monospace code box. | Test the child's `className` for `language-mermaid`. |
| 2.18 | [AuthProvider.tsx:68](frontend/src/components/AuthProvider.tsx:68) | An API outage on load looks like being logged out and redirects to `/login`. | Add an error state with a retry. |
| 2.19 | [ExtractStep.tsx:216](frontend/src/components/document/ExtractStep.tsx:216) | Skipped and cancelled engines are labelled "Running". | Add the two labels. |

Smaller items: AI-correct polling is not cancelled on unmount; the attachment
upload has no retry on a stale CSRF token; drag-and-drop bypasses the file type
filter and there is no client-side size check; a few strings are English only
(`"Breadcrumb"`, `"Progress"`, `"network error"`, custom-field labels always use
`label_en`); 16 i18n keys are unused; the streaming answer has no `aria-live`
region.

---

## 3. Deployment readiness

The README says to "change it for any shared deployment", but the stack has no
production mode yet. Before it runs anywhere other than a developer's machine:

- **Add `config/settings/prod.py`.** Today `DJANGO_DEBUG` defaults to `1`,
  `ALLOWED_HOSTS` to `*`, and there is only `dev.py` and `test.py`. A production
  module should force `DEBUG = False`, require the secret key and hosts, and set
  `SECURE_HSTS_SECONDS`, `SECURE_SSL_REDIRECT`, `SECURE_PROXY_SSL_HEADER` and
  secure cookies.
- **Do not seed a published password.** `NCST_system` / `NCST@12345` is in the
  README, `.env.example`, `docker-compose.yml` and the seed command, and the
  account is a superuser. Refuse to seed the default when `DEBUG` is off, or
  require `DEFAULT_PASSWORD` to be set.
- **Build the frontend.** [docker/frontend/Dockerfile](docker/frontend/Dockerfile)
  runs `next dev` with the source bind-mounted. Add a multi-stage build
  (`next build`, then `next start` or standalone output) with
  `NEXT_PUBLIC_API_BASE` as a build argument, and keep the dev setup as a compose
  override.
- **Separate dev and prod compose.** The backend bind-mounts `./backend`, runs as
  root, publishes Postgres on host port 5433 and the API on 8000 on all
  interfaces. Production should use the image's own code, a non-root user, no
  published database port, and a TLS reverse proxy configured not to buffer SSE.
- **Split requirements.** `pytest`, `weasyprint`, `jiwer` and `responses` are
  installed in the runtime image.
- **Worker supervision.** One container runs two Celery workers with `&` and
  `wait`; if one dies the container stays "up". Use two services, each with a
  healthcheck.
- **Backups.** Nothing backs up the `pgdata` or `media` volumes.

---

## 4. Tooling and repository hygiene

- **No CI.** Add a workflow that runs `make test`, `make test-runner`,
  `tsc --noEmit` and a frontend build.
- **`npm run lint` does not work.** ESLint is not installed and there is no
  config. Add `eslint` + `eslint-config-next` and a `typecheck` script.
- **No Python linter or formatter config** (`ruff`, `mypy`), and no
  `pytest.ini` / `pyproject.toml`; tests rely on the environment variable set by
  `make test`.
- **No root `.dockerignore`.** The backend build context is the repository root,
  so `.git`, `frontend/node_modules` and `.env` are sent to the Docker daemon on
  every build.
- **`--profile all-engines` cannot build.** `docker/ocr-easyocr/` is empty and
  `docker/ocr-paddle/` does not exist (the README already lists these as not
  built). Remove the profiles or add the servers.
- **`docs/` is git-ignored** but holds the architecture diagram and an article.
  Decide whether it should be tracked.
- **`make fixtures`** is missing from the Makefile's `.PHONY` list.

---

## 5. Test coverage worth adding

- Re-indexing a second revision (would have caught 1.1).
- Multi-turn chat with a real checkpointer (would have caught 1.2).
- Preprocess failure inside an OCR batch, and batch cancel (1.5, 2.11).
- `finalize_text` from each document state (2.2).
- Document delete removes files (2.1).
- Malformed bodies to `chat_stream`; non-UTF-8 text attachments.
- Any frontend test at all: there are none. Start with `safeNext`, the SSE frame
  parser and the chat stream lifecycle.

---

## 6. Checked and found correct

- Ownership filtering on documents, OCR batches and runs, correction jobs,
  metadata, conversations, attachments, generated files and search.
- Chat thread ids are reached only after the owner check; the agent always gets
  an explicit list of the caller's document ids, and an empty list returns
  nothing.
- CSRF is enforced on login, the chat stream and every DRF write; the session key
  rotates on login and other sessions end on password change.
- Upload type comes from file content, not the client's name or header; storage
  paths are built from server-generated UUIDs.
- Code runner: internal-only network, non-root, read-only filesystem, no
  capabilities, per-job rlimits, file type and size caps; SVG and non-raster
  output is served as a download with `nosniff`.
- Markdown has no raw HTML; Mermaid runs with `securityLevel: "strict"`; links
  block `javascript:` URLs.
- Raw SQL in retrieval is fully parameterized.
- Both locales have the same 308 keys; RTL uses logical properties throughout.
