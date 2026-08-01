#!/usr/bin/env bash
# Ollama runs on the host by design; fail fast with a clear message if it isn't.
set -euo pipefail

URL="${OLLAMA_HOST_URL:-http://localhost:11434}"

if curl -fsS --max-time 5 "${URL}/api/tags" >/dev/null 2>&1; then
  echo "✓ Ollama reachable at ${URL}"
  exit 0
fi

cat <<EOF
✗ Ollama is not reachable at ${URL}

  This stack expects Ollama on the HOST so it can use the GPU — Docker on macOS
  has no GPU passthrough, so an in-container Ollama would be CPU-only.

  Start it:            ollama serve
  Then pull models:    make warmup

  Or run fully contained (slower, CPU-only):
      docker compose --profile bundled-ollama up
EOF
exit 1
