"""Metadata extraction: plan, extract, validate, degrade.

Two calls, deliberately:

  Call A (plan)    — the LLM proposes which fields this document supports. This
                     is the "tell the user what information is available" step.
  Call B (extract) — the core schema PLUS a custom_fields object closed to
                     exactly the accepted keys. Closing it at generation time is
                     what makes the open part reliable.

The retry ladder never lets metadata failure block the pipeline: a document with
degraded metadata still indexes and is still chattable. Metadata blocking chat
would be the worst possible failure mode for this product.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from django.conf import settings
from pydantic import ValidationError

from common.ollama import OllamaError, get_client
from enrichment.schemas import (
    DocumentMetadata,
    ExtractionPlanOut,
    build_extraction_schema,
    core_schema,
    plan_schema,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"

PLAN_PROMPT = """Analyze this document and propose the structured fields that could be extracted from it.

Rules:
- Only propose a field if its value is ACTUALLY PRESENT in the text below.
- `example_value` must be copied verbatim from the document.
- Use snake_case for `key`.
- Provide both an English and an Arabic label.
- Propose at most 12 fields. Prefer the ones a person would search by.
- Do not propose fields already covered by the standard schema (title, summary,
  keywords, document date, general person/organization lists).

DOCUMENT:
{text}"""

EXTRACT_PROMPT = """Extract structured metadata from this document.

Rules:
- Use ONLY information present in the document. Never invent a value.
- If a field is not present, use null (or an empty list).
- `title` must be in the document's ORIGINAL script. `title_translit` must be Latin script.
- Write `summary_short` and `summary_long` in the document's primary language.
- Dates must be ISO 8601 (YYYY-MM-DD). If a date is Hijri, convert it to Gregorian
  and also keep the original string in dates.mentioned.
- Copy identifiers (reference numbers, amounts) exactly as written.

DOCUMENT:
{text}"""

REPAIR_PROMPT = """The JSON you produced was invalid. Fix it.

Validation errors:
{errors}

Your previous output:
{previous}

Return ONLY corrected JSON matching the required schema."""

# Beyond this the KV cache pressure on a 32GB machine is not worth it; longer
# documents go through the map-reduce path instead.
MAX_DIRECT_CHARS = 24000


def _client():
    return get_client()


def plan_fields(text: str, *, model: str | None = None) -> dict:
    """Call A. Returns {detected_doc_type, proposed_fields[]}."""
    model = model or settings.LLM_MODEL
    # A head+tail sample is enough to know what KIND of document this is, and
    # keeps the planning call fast.
    sample = text[:3000] + ("\n...\n" + text[-1000:] if len(text) > 4000 else "")

    parsed, raw = _client().generate_json(
        model, PLAN_PROMPT.format(text=sample), plan_schema()
    )
    if parsed is None:
        logger.warning("extraction plan was not valid JSON: %.300s", raw)
        return {"detected_doc_type": "", "proposed_fields": [], "error": "invalid json"}

    try:
        plan = ExtractionPlanOut.model_validate(parsed)
    except ValidationError as exc:
        logger.warning("extraction plan failed validation: %s", exc)
        return {"detected_doc_type": "", "proposed_fields": [], "error": str(exc)}

    return {
        "detected_doc_type": plan.detected_doc_type,
        "proposed_fields": [f.model_dump() for f in plan.proposed_fields],
    }


def merge_required_fields(proposed: list[dict], required: list[dict],
                          min_confidence: float = 0.6) -> list[dict]:
    """Accept high-confidence proposals plus everything the user declared."""
    accepted = [f for f in proposed if f.get("confidence", 0) >= min_confidence]
    by_key = {f["key"]: f for f in accepted}

    for field in required or []:
        key = field.get("key") or re.sub(r"\W+", "_", str(field.get("label_en", ""))).lower()
        if not key:
            continue
        entry = by_key.get(key, {})
        by_key[key] = {
            "key": key,
            "label_en": field.get("label_en") or entry.get("label_en") or key,
            "label_ar": field.get("label_ar") or entry.get("label_ar") or key,
            "type": field.get("type") or entry.get("type") or "string",
            "why": field.get("why") or entry.get("why") or "requested by the user",
            "example_value": entry.get("example_value", ""),
            "confidence": 1.0,
            "user_required": True,
        }
    return list(by_key.values())


def extract(text: str, accepted_fields: list[dict], *, model: str | None = None) -> dict:
    """Call B plus the retry ladder. Always returns a usable result dict."""
    model = model or settings.LLM_MODEL
    client = _client()
    schema = build_extraction_schema(accepted_fields)

    if len(text) > MAX_DIRECT_CHARS:
        body = _map_reduce(text, model)
    else:
        body = text

    prompt = EXTRACT_PROMPT.format(text=body)
    attempts = 0
    last_raw = ""
    last_errors = ""

    # Rung 1 and 2: identical call, then a repair call quoting the validator's
    # own errors. The repair call has by far the highest hit rate.
    for rung in range(3):
        attempts += 1
        try:
            if rung < 2:
                parsed, raw = client.generate_json(model, prompt, schema)
            else:
                parsed, raw = client.generate_json(
                    model,
                    REPAIR_PROMPT.format(errors=last_errors, previous=last_raw[:4000]),
                    schema,
                )
        except OllamaError as exc:
            logger.warning("extraction call failed (rung %s): %s", rung, exc)
            last_errors = str(exc)
            continue

        last_raw = raw
        if parsed is None:
            last_errors = "output was not parseable JSON"
            continue

        custom = parsed.pop("custom_fields", {}) or {}
        try:
            metadata = DocumentMetadata.model_validate(parsed)
        except ValidationError as exc:
            last_errors = json.dumps(exc.errors()[:8], default=str)[:2000]
            logger.info("metadata validation failed on rung %s: %s", rung, last_errors[:300])
            continue

        return {
            "metadata": metadata,
            "custom_fields": custom,
            "raw": raw,
            "attempts": attempts,
            "is_partial": False,
            "quality_flags": [],
        }

    # Rung 4: degrade. A document with partial metadata still indexes and is
    # still chattable; that matters more than a perfect record.
    logger.warning("metadata extraction degraded after %s attempts", attempts)
    return _degrade(text, model, attempts, last_raw)


def _degrade(text: str, model: str, attempts: int, last_raw: str) -> dict:
    """Ask for only the five fields the rest of the system truly needs."""
    minimal = {
        "type": "object",
        "additionalProperties": False,
        "required": ["doc_type", "title", "primary_language", "languages", "summary_short"],
        "properties": {
            "doc_type": core_schema()["properties"]["doc_type"],
            "title": {"type": "string"},
            "primary_language": {"type": "string"},
            "languages": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
            "summary_short": {"type": "string", "maxLength": 400},
        },
    }
    try:
        parsed, raw = _client().generate_json(
            model, EXTRACT_PROMPT.format(text=text[:8000]), minimal
        )
        if parsed:
            metadata = DocumentMetadata.model_validate(parsed)
            return {
                "metadata": metadata,
                "custom_fields": {},
                "raw": raw,
                "attempts": attempts + 1,
                "is_partial": True,
                "quality_flags": ["metadata_degraded"],
            }
    except (OllamaError, ValidationError) as exc:
        logger.warning("degraded extraction also failed: %s", exc)

    # Absolute floor: a record built without the model at all, so indexing and
    # chat still work.
    from common.arabic import detect_lang

    lang = detect_lang(text)
    primary = "ar" if lang == "ar" else "en"
    return {
        "metadata": DocumentMetadata(
            doc_type="other",
            title=(text.strip().split("\n") or [""])[0][:200] or "Untitled document",
            title_translit=None,
            primary_language=primary,
            languages=[primary],
            summary_short=text.strip()[:300],
            summary_long="",
            keywords=[],
            topics=[],
        ),
        "custom_fields": {},
        "raw": last_raw,
        "attempts": attempts + 1,
        "is_partial": True,
        "quality_flags": ["metadata_degraded", "metadata_unavailable"],
    }


def _map_reduce(text: str, model: str) -> str:
    """Condense a long document to a body the extractor can handle in one call."""
    pages = text.split("\f")
    windows, current = [], ""
    for page in pages:
        if len(current) + len(page) > 8000 and current:
            windows.append(current)
            current = page
        else:
            current = f"{current}\f{page}" if current else page
    if current:
        windows.append(current)

    partials = []
    for index, window in enumerate(windows[:8]):
        try:
            summary = _client().generate(
                model,
                "Summarize the key facts in this section in 3 sentences, and list any "
                "names, dates, reference numbers and amounts verbatim.\n\n" + window,
                options={"num_predict": 400},
            )
            partials.append(f"[section {index + 1}]\n{summary}")
        except OllamaError:
            continue

    head = text[:6000]
    return head + "\n\n--- CONDENSED REMAINDER ---\n" + "\n\n".join(partials)


HIJRI_MONTHS = {
    "محرم": 1, "صفر": 2, "ربيع الأول": 3, "ربيع الثاني": 4, "جمادى الأولى": 5,
    "جمادى الآخرة": 6, "رجب": 7, "شعبان": 8, "رمضان": 9, "شوال": 10,
    "ذو القعدة": 11, "ذو الحجة": 12,
}


def parse_date(value: str | None) -> date | None:
    """Parse an ISO date, or a Hijri date written in Arabic.

    Real Arabic documents carry both calendars, frequently on the same line.
    """
    if not value or not isinstance(value, str):
        return None

    text = value.strip()
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    if "هـ" in text or "هجري" in text:
        from common.arabic import ar_normalize

        normalized = ar_normalize(text)
        numbers = re.findall(r"\d+", normalized)
        month = next(
            (num for name, num in HIJRI_MONTHS.items() if ar_normalize(name) in normalized),
            None,
        )
        if month and len(numbers) >= 2:
            day, year = int(numbers[0]), int(numbers[-1])
            try:
                from hijri_converter import Hijri

                return Hijri(year, month, day).to_gregorian().datetime().date()
            except Exception:
                return None
    return None
