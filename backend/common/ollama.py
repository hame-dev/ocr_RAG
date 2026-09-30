"""Thin Ollama client.

Ollama runs on the HOST (Metal GPU); containers reach it via
host.docker.internal. Everything that touches it — VLM OCR, AI correction,
metadata extraction, embeddings — funnels through here so timeouts, options and
error handling are consistent.
"""
from __future__ import annotations

import base64
import json
import logging
import math
from typing import Any, Iterator

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


class OllamaUnavailable(OllamaError):
    """Ollama is unreachable — distinct from a model-level failure."""


def _b64(path: str) -> str:
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


class OllamaClient:
    def __init__(self, base_url: str | None = None, timeout: float = 600.0):
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self.timeout = timeout

    # ---- introspection ------------------------------------------------------

    def tags(self) -> list[dict]:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=10.0)
            r.raise_for_status()
            return r.json().get("models", [])
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"ollama unreachable at {self.base_url}: {exc}")

    def has_model(self, model: str) -> bool:
        names = {m.get("name", "") for m in self.tags()}
        # Ollama reports "name:tag"; accept a bare name matching any tag.
        return model in names or any(n.split(":")[0] == model for n in names)

    def show(self, model: str) -> dict:
        r = httpx.post(f"{self.base_url}/api/show", json={"model": model}, timeout=20.0)
        r.raise_for_status()
        return r.json()

    def capabilities(self, model: str) -> list[str]:
        try:
            return self.show(model).get("capabilities", []) or []
        except httpx.HTTPError:
            return []

    def supports_vision(self, model: str) -> bool:
        return "vision" in self.capabilities(model)

    # ---- generation ---------------------------------------------------------

    def generate(
        self,
        model: str,
        prompt: str,
        *,
        images: list[str] | None = None,
        system: str | None = None,
        format: dict | str | None = None,
        options: dict | None = None,
        think: bool | None = False,
        timeout: float | None = None,
    ) -> str:
        """Single-turn generation. `images` are file paths, encoded here."""
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            # temperature 0 for everything structured; these are extraction
            # tasks, not creative ones.
            "options": {"temperature": 0, "num_ctx": settings.LLM_NUM_CTX, **(options or {})},
        }
        if images:
            body["images"] = [_b64(p) for p in images]
        if system:
            body["system"] = system
        if format is not None:
            body["format"] = format
        if think is not None:
            # qwen3.5 has a thinking mode. It wastes latency on structured tasks
            # and its blocks leak into parsing, so it is off by default.
            body["think"] = think

        try:
            r = httpx.post(
                f"{self.base_url}/api/generate",
                json=body,
                timeout=timeout or self.timeout,
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"generate failed for {model}: {exc}") from exc

        return r.json().get("response", "")

    def generate_json(
        self,
        model: str,
        prompt: str,
        schema: dict,
        **kwargs,
    ) -> tuple[dict | list | None, str]:
        """Grammar-constrained generation. Returns (parsed_or_None, raw_text)."""
        raw = self.generate(model, prompt, format=schema, **kwargs)
        try:
            return json.loads(raw), raw
        except json.JSONDecodeError:
            return None, raw

    def score_yes_no(
        self,
        model: str,
        prompt: str,
        *,
        timeout: float = 5.0,
        num_ctx: int = 2048,
    ) -> float:
        """P(yes) / (P(yes) + P(no)) for the next token after a raw prompt.

        How a Qwen3-Reranker is scored: one generated token, read from the
        logprobs rather than the text. "yes" is summed over its spellings
        ("yes", "Yes", " yes"). 0.0 when neither answer is among the top tokens.
        The small num_ctx matters: without it a 0.6B model allocates the
        default 16k KV cache for a ~500-token prompt.
        """
        body = {
            "model": model,
            "prompt": prompt,
            "raw": True,
            "stream": False,
            "logprobs": True,
            "top_logprobs": 10,
            "keep_alive": "30m",
            "options": {"num_predict": 1, "temperature": 0, "num_ctx": num_ctx},
        }
        try:
            r = httpx.post(f"{self.base_url}/api/generate", json=body, timeout=timeout)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"score failed for {model}: {exc}") from exc

        steps = r.json().get("logprobs") or []
        if not steps:
            raise OllamaError(f"{model} returned no logprobs (Ollama too old?)")
        first = steps[0]
        candidates = {t["token"]: t["logprob"] for t in first.get("top_logprobs") or []}
        candidates.setdefault(first.get("token", ""), first.get("logprob", float("-inf")))

        def mass(word: str) -> float:
            return sum(math.exp(lp) for token, lp in candidates.items() if token.strip().lower() == word)

        p_yes, p_no = mass("yes"), mass("no")
        return p_yes / (p_yes + p_no) if p_yes + p_no > 0 else 0.0

    def chat_stream(
        self,
        model: str,
        messages: list[dict],
        *,
        options: dict | None = None,
    ) -> Iterator[str]:
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"num_ctx": settings.LLM_NUM_CTX, **(options or {})},
        }
        with httpx.stream(
            "POST", f"{self.base_url}/api/chat", json=body, timeout=self.timeout
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                token = chunk.get("message", {}).get("content", "")
                if token:
                    yield token

    # ---- embeddings ---------------------------------------------------------

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Embed a batch.

        bge-m3 takes NO instruction prefix — unlike bge-*-v1.5 and e5-*,
        prepending "query:"/"passage:" measurably degrades it. Ollama already
        returns L2-normalized vectors for bge-m3; we normalize defensively
        anyway so cosine distance is exact regardless of model.
        """
        model = model or settings.EMBED_MODEL
        if not texts:
            return []
        try:
            r = httpx.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": texts},
                timeout=self.timeout,
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"embed failed for {model}: {exc}") from exc

        vectors = r.json().get("embeddings", [])
        return [_l2(v) for v in vectors]


def _l2(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        return vec
    return [x / norm for x in vec]


_client: OllamaClient | None = None


def get_client() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client
