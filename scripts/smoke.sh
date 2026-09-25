#!/usr/bin/env bash
# End-to-end proof: upload -> preprocess -> OCR chord -> select -> finalize ->
# enrich -> index -> hybrid search -> LangGraph tool call + cited SSE answer.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
API_BASE=${API_BASE:-http://localhost:8000}
FIXTURE=${SMOKE_FIXTURE:-$PROJECT_DIR/backend/tests/fixtures/scan_clean.pdf}
ENGINES=${SMOKE_ENGINES:-chandra_ollama}
TIMEOUT_S=${SMOKE_TIMEOUT_S:-300}
POLL_S=2
# The API requires a session. Create the account once with:
#   make createuser U=smoke
SMOKE_USERNAME=${SMOKE_USERNAME:-}
SMOKE_PASSWORD=${SMOKE_PASSWORD:-}

fail() {
  printf 'SMOKE FAILED: %s\n' "$*" >&2
  exit 1
}

for command_name in curl jq; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required"
done
[ -n "$SMOKE_USERNAME" ] && [ -n "$SMOKE_PASSWORD" ] \
  || fail "set SMOKE_USERNAME and SMOKE_PASSWORD (create one with: make createuser U=smoke)"

COOKIE_JAR=$(mktemp)
trap 'rm -f "$COOKIE_JAR"' EXIT
CSRF_TOKEN=""

# Authenticated curl: session cookie from the jar + CSRF header for writes.
acurl() {
  curl -fsS -b "$COOKIE_JAR" -c "$COOKIE_JAR" -H "X-CSRFToken: $CSRF_TOKEN" "$@"
}

if [ ! -f "$FIXTURE" ]; then
  printf 'Fixture missing; generating it...\n'
  make -C "$PROJECT_DIR" fixtures
fi

printf '1/9 Checking deep health...\n'
health_payload=$(curl -fsS "$API_BASE/api/health/deep/") || fail "deep health is not OK"
[ "$(printf '%s' "$health_payload" | jq -r .status)" = "ok" ] || {
  printf '%s\n' "$health_payload" | jq . >&2
  fail "stack is degraded"
}

printf '    Signing in as %s...\n' "$SMOKE_USERNAME"
CSRF_TOKEN=$(curl -fsS -c "$COOKIE_JAR" "$API_BASE/api/auth/csrf/" | jq -er .csrfToken)
login_payload=$(jq -nc --arg u "$SMOKE_USERNAME" --arg p "$SMOKE_PASSWORD" \
  '{username:$u,password:$p}')
CSRF_TOKEN=$(acurl -X POST "$API_BASE/api/auth/login/" \
  -H 'Content-Type: application/json' -d "$login_payload" | jq -er .csrfToken) \
  || fail "login failed for $SMOKE_USERNAME"

printf '2/9 Uploading %s...\n' "$(basename "$FIXTURE")"
upload_payload=$(acurl -X POST "$API_BASE/api/documents/" \
  -F "file=@$FIXTURE" \
  -F "title=Automated smoke $(date +%s)")
document_id=$(printf '%s' "$upload_payload" | jq -er .id)

max_attempts=$((TIMEOUT_S / POLL_S))
attempt=1
while [ "$attempt" -le "$max_attempts" ]; do
  document_payload=$(acurl "$API_BASE/api/documents/$document_id/")
  document_status=$(printf '%s' "$document_payload" | jq -r .status)
  case "$document_status" in
    preprocessed) break ;;
    failed)
      printf '%s\n' "$document_payload" | jq . >&2
      fail "preprocessing failed"
      ;;
  esac
  sleep "$POLL_S"
  attempt=$((attempt + 1))
done
[ "$document_status" = "preprocessed" ] || fail "preprocessing timed out"

printf '3/9 Running OCR engines: %s...\n' "$ENGINES"
ocr_request=$(jq -nc --arg engines "$ENGINES" \
  '{engines:($engines | split(",")), languages:["ara","eng"]}')
batch_payload=$(acurl -X POST "$API_BASE/api/documents/$document_id/ocr/" \
  -H 'Content-Type: application/json' -d "$ocr_request")
batch_id=$(printf '%s' "$batch_payload" | jq -er .id)

attempt=1
while [ "$attempt" -le "$max_attempts" ]; do
  batch_payload=$(acurl "$API_BASE/api/ocr/batches/$batch_id/")
  batch_status=$(printf '%s' "$batch_payload" | jq -r .status)
  run_statuses=$(printf '%s' "$batch_payload" | \
    jq -r '[.runs[] | .engine_name + ":" + .status] | join(", ")')
  printf '    OCR %s (%s)\n' "$batch_status" "$run_statuses"
  case "$batch_status" in
    done) break ;;
    failed|cancelled)
      printf '%s\n' "$batch_payload" | jq . >&2
      fail "OCR batch did not complete"
      ;;
  esac
  sleep "$POLL_S"
  attempt=$((attempt + 1))
done
[ "$batch_status" = "done" ] || fail "OCR timed out"

printf '4/9 Selecting the ranked baseline and finalizing text...\n'
compare_payload=$(acurl "$API_BASE/api/ocr/batches/$batch_id/compare/")
baseline_id=$(printf '%s' "$compare_payload" | jq -er .baseline_run_id)
select_request=$(jq -nc --arg run_id "$baseline_id" '{run_id:$run_id}')
revision_payload=$(acurl -X POST \
  "$API_BASE/api/documents/$document_id/text/select/" \
  -H 'Content-Type: application/json' -d "$select_request")
revision_no=$(printf '%s' "$revision_payload" | jq -er .revision_no)
acurl -X POST "$API_BASE/api/documents/$document_id/text/finalize/" \
  -H 'Content-Type: application/json' \
  -d "$(jq -nc --argjson revision_no "$revision_no" '{revision_no:$revision_no}')" \
  >/dev/null

printf '5/9 Enriching metadata and indexing...\n'
acurl -X POST "$API_BASE/api/documents/$document_id/enrich/" \
  -H 'Content-Type: application/json' -d '{"auto_index":true}' >/dev/null
attempt=1
while [ "$attempt" -le "$max_attempts" ]; do
  document_payload=$(acurl "$API_BASE/api/documents/$document_id/")
  document_status=$(printf '%s' "$document_payload" | jq -r .status)
  printf '    document %s\n' "$document_status"
  case "$document_status" in
    ready) break ;;
    failed)
      printf '%s\n' "$document_payload" | jq . >&2
      fail "enrichment or indexing failed"
      ;;
  esac
  sleep "$POLL_S"
  attempt=$((attempt + 1))
done
[ "$document_status" = "ready" ] || fail "enrichment/indexing timed out"

printf '6/9 Validating structured metadata...\n'
metadata_payload=$(acurl "$API_BASE/api/documents/$document_id/metadata/")
printf '%s' "$metadata_payload" | jq -e '.doc_type and .title and .model_id' >/dev/null \
  || fail "metadata is incomplete"

printf '7/9 Validating cross-lingual hybrid retrieval...\n'
search_payload=$(acurl -X POST "$API_BASE/api/search/" \
  -H 'Content-Type: application/json' \
  -d '{"query":"ما قيمة الإيجار السنوي؟","top_k":3}')
[ "$(printf '%s' "$search_payload" | jq -r .count)" -gt 0 ] \
  || fail "hybrid search returned no hits"
printf '%s' "$search_payload" | jq -e --arg document_id "$document_id" \
  '.results | any(.document_id == $document_id)' >/dev/null \
  || fail "hybrid search did not retrieve the uploaded document"

printf '8/9 Running the LangGraph SSE agent...\n'
conversation_request=$(jq -nc --arg document_id "$document_id" \
  '{scope:"selected",document_ids:[$document_id]}')
conversation_payload=$(acurl -X POST "$API_BASE/api/conversations/" \
  -H 'Content-Type: application/json' -d "$conversation_request")
conversation_id=$(printf '%s' "$conversation_payload" | jq -er .id)
stream_payload=$(acurl -N --max-time "$TIMEOUT_S" -X POST \
  "$API_BASE/api/conversations/$conversation_id/stream/" \
  -H 'Content-Type: application/json' \
  -d '{"content":"What is the annual rent amount and when does the lease start?"}')
printf '%s' "$stream_payload" | grep -q 'event: tool_start' \
  || fail "agent did not call a tool"
printf '%s' "$stream_payload" | grep -q 'event: done' \
  || fail "agent stream did not finish"
printf '%s' "$stream_payload" | grep -q '"citation_mode": "explicit"' \
  || fail "agent answer did not contain a resolved explicit citation"

printf '9/9 Verifying persisted chat transcript and General mode...\n'
conversation_payload=$(acurl "$API_BASE/api/conversations/$conversation_id/")
[ "$(printf '%s' "$conversation_payload" | jq -r .message_count)" -eq 2 ] \
  || fail "expected one user and one assistant message"

general_id=$(acurl -X POST "$API_BASE/api/conversations/" \
  -H 'Content-Type: application/json' -d '{"scope":"all"}' | jq -er .id)
general_payload=$(acurl -N --max-time "$TIMEOUT_S" -X POST \
  "$API_BASE/api/conversations/$general_id/stream/" \
  -H 'Content-Type: application/json' \
  -d '{"content":"In one sentence, what is OCR?","chat_mode":"general"}')
printf '%s' "$general_payload" | grep -q 'event: done' \
  || fail "general-mode stream did not finish"
if printf '%s' "$general_payload" | grep -q 'event: tool_start'; then
  fail "general mode must not call document tools"
fi

printf '\nSMOKE PASSED\n'
printf 'document_id=%s\n' "$document_id"
printf 'batch_id=%s\n' "$batch_id"
printf 'conversation_id=%s\n' "$conversation_id"
printf 'ocr_ranking=%s\n' "$(printf '%s' "$compare_payload" | jq -r '.ranking | join(", ")')"
printf 'doc_type=%s\n' "$(printf '%s' "$metadata_payload" | jq -r .doc_type)"
