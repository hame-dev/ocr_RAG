"""Stage 2: one LLM call per WINDOW of consecutive chunks, not per chunk.

A window is up to ~6 chunks of the same section. The model writes a one- or
two-sentence summary of that stretch of the section plus a few keywords; every
chunk in the window shares them. At a few tokens per second on the current
hardware, per-chunk calls would take weeks for the whole library; per-window
calls on a small model take hours, run in the background, and are cached by
content so a re-index costs nothing.

Pure functions only; the task that applies the results lives in rag.tasks.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Protocol, Sequence

logger = logging.getLogger(__name__)

SUMMARY_MAX_CHARS = 300
MAX_KEYWORDS = 10
KEYWORD_MAX_CHARS = 60

CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_KEYWORDS},
    },
    "required": ["summary", "keywords"],
}

LANGUAGE_NAMES = {"ar": "Arabic", "en": "English"}

CONTEXT_PROMPT = """You are indexing a document for search. Below is one stretch of a section.

Write:
- "summary": one or two sentences (at most {max_chars} characters) saying what this
  stretch is about and how it fits the document. {language_rule}
- "keywords": up to {max_keywords} short search terms (names, amounts, legal or
  technical terms) that actually appear in or are clearly implied by the text.

Use ONLY the text below. Do not invent facts.

DOCUMENT: {title}
DOCUMENT SUMMARY: {doc_summary}
SECTION: {section}

TEXT:
{text}"""

REPAIR_PROMPT = """Your previous output was invalid: {error}

Previous output:
{previous}

Return ONLY a JSON object with a non-empty string "summary" and an array of
strings "keywords", for this task:

{task}"""


class _Chunkish(Protocol):
    text: str
    section_path: str
    token_count: int


def build_windows(
    chunks: Sequence[_Chunkish], *, max_tokens: int = 3000, max_chunks: int = 6
) -> list[list[_Chunkish]]:
    """Group consecutive chunks. A new window starts on a section change, at
    `max_chunks`, or when the next chunk would exceed `max_tokens`."""
    windows: list[list[_Chunkish]] = []
    current: list[_Chunkish] = []
    tokens = 0
    for chunk in chunks:
        if current and (
            chunk.section_path != current[0].section_path
            or len(current) >= max_chunks
            or tokens + (chunk.token_count or 0) > max_tokens
        ):
            windows.append(current)
            current, tokens = [], 0
        current.append(chunk)
        tokens += chunk.token_count or 0
    if current:
        windows.append(current)
    return windows


def window_text(window: Sequence[_Chunkish]) -> str:
    return "\n\n".join(chunk.text for chunk in window)


def window_key(title: str, section_path: str, text: str, model: str, version: str) -> str:
    """Cache key. Title and section are part of it: the same text under a
    different header is a different summary."""
    payload = json.dumps([title, section_path, text, model, version], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def merge_keywords(*lists: Sequence[str], cap: int = 12) -> list[str]:
    """Union in order, case-insensitive, capped."""
    out: list[str] = []
    seen: set[str] = set()
    for keywords in lists:
        for keyword in keywords or []:
            keyword = " ".join(str(keyword).split())
            if keyword and keyword.lower() not in seen:
                seen.add(keyword.lower())
                out.append(keyword)
    return out[:cap]


def _validate(parsed) -> tuple[dict | None, str]:
    if not isinstance(parsed, dict):
        return None, "output was not a JSON object"
    summary = parsed.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None, '"summary" must be a non-empty string'
    keywords = parsed.get("keywords")
    if not isinstance(keywords, list):
        return None, '"keywords" must be an array of strings'
    cleaned = [
        " ".join(k.split()) for k in keywords
        if isinstance(k, str) and k.strip() and len(k.strip()) <= KEYWORD_MAX_CHARS
    ]
    return {
        "summary": " ".join(summary.split())[:SUMMARY_MAX_CHARS],
        "keywords": merge_keywords(cleaned, cap=MAX_KEYWORDS),
    }, ""


def summarize_window(
    client,
    model: str,
    doc_title: str,
    doc_summary: str,
    section_path: str,
    text: str,
    primary_language: str,
) -> dict | None:
    """{summary, keywords} for one window, or None after one failed repair.

    OllamaError propagates: an unreachable model is the task's problem, not a
    reason to record an empty summary.
    """
    language = LANGUAGE_NAMES.get((primary_language or "").lower())
    language_rule = (
        f"Write the summary in {language}." if language
        else "Write the summary in the main language of the text."
    )
    task = CONTEXT_PROMPT.format(
        max_chars=SUMMARY_MAX_CHARS,
        max_keywords=MAX_KEYWORDS,
        language_rule=language_rule,
        title=doc_title or "(untitled)",
        doc_summary=(doc_summary or "")[:400] or "(none)",
        section=section_path or "(none)",
        text=text,
    )
    parsed, raw = client.generate_json(model, task, CONTEXT_SCHEMA)
    result, error = _validate(parsed)
    if result:
        return result

    logger.info("chunk context invalid (%s); repairing once", error)
    parsed, raw = client.generate_json(
        model, REPAIR_PROMPT.format(error=error, previous=(raw or "")[:2000], task=task), CONTEXT_SCHEMA
    )
    result, error = _validate(parsed)
    if not result:
        logger.warning("chunk context still invalid after repair: %s", error)
    return result
