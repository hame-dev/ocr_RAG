#!/usr/bin/env bash
# Pull the host Ollama models this system needs.
#
# Ollama runs on the HOST, not in a container: Docker on macOS has no GPU
# passthrough, so an in-container Ollama would be CPU-only and roughly 10x
# slower. Containers reach the host at host.docker.internal:11434.
set -euo pipefail

OLLAMA_HOST_URL="${OLLAMA_HOST_URL:-http://localhost:11434}"

REQUIRED=(
  "qwen3.5:9b"                    # agent, AI correction (vision), metadata
  "bge-m3"                        # embeddings — multilingual, 1024-dim
  "fredrezones55/chandra-ocr-2:latest" # default layout-aware OCR
  "melashri/surya-ocr-2:q4_k_m"   # layout-aware OCR, 609MB
  "${RERANK_MODEL:-qwen3.5:4b}"         # search reranker / judge (RERANK_MODEL)
  "${CHUNK_CONTEXT_MODEL:-qwen3.5:4b}"  # background chunk context (CHUNK_CONTEXT_MODEL)
)

OPTIONAL=(
  "melashri/qari-ocr:0.4.0-q4_k_m"  # Arabic-specialist OCR (Phase 2)
)

echo "==> Checking Ollama at ${OLLAMA_HOST_URL}"
if ! curl -fsS --max-time 5 "${OLLAMA_HOST_URL}/api/tags" >/dev/null 2>&1; then
  echo "!! Ollama is not reachable at ${OLLAMA_HOST_URL}"
  echo "   Install from https://ollama.com and make sure it is running."
  exit 1
fi

# `ollama list` shows an untagged pull as "name:latest".
have() { ollama list 2>/dev/null | awk '{print $1}' | grep -Fxq -e "$1" -e "$1:latest"; }

for model in "${REQUIRED[@]}"; do
  if have "$model"; then
    echo "  ✓ ${model}"
  else
    echo "  ↓ pulling ${model} ..."
    ollama pull "$model"
  fi
done

if [[ "${WITH_OPTIONAL:-0}" == "1" ]]; then
  for model in "${OPTIONAL[@]}"; do
    have "$model" && echo "  ✓ ${model}" || { echo "  ↓ pulling ${model} ..."; ollama pull "$model"; }
  done
else
  echo "  · optional models skipped (run with WITH_OPTIONAL=1 to pull them)"
fi

echo "==> Done."
