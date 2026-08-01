"""The standardized metadata schema.

Every document gets the SAME core fields. That is what makes metadata filtering
work across the corpus: if one document has `vendor`, another `supplier` and a
third `company_name`, no filter can target them.

Free-form extras live in `custom_fields`, which is closed into a concrete typed
object at generation time (see build_extraction_schema) — an open
`dict[str, Any]` cannot be grammar-constrained, so this is what makes the open
part reliable rather than a source of drift.
"""
from __future__ import annotations

import copy
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class DocType(str, Enum):
    CONTRACT = "contract"
    INVOICE = "invoice"
    RECEIPT = "receipt"
    LETTER = "letter"
    REPORT = "report"
    FORM = "form"
    ID_DOCUMENT = "id_document"
    CERTIFICATE = "certificate"
    ACADEMIC = "academic"
    LEGAL = "legal"
    MEDICAL = "medical"
    MANUAL = "manual"
    OTHER = "other"


class Entities(BaseModel):
    model_config = {"extra": "forbid"}
    persons: list[str] = Field(default_factory=list, max_length=40)
    organizations: list[str] = Field(default_factory=list, max_length=40)
    locations: list[str] = Field(default_factory=list, max_length=40)
    other: list[str] = Field(default_factory=list, max_length=40)


class Dates(BaseModel):
    model_config = {"extra": "forbid"}
    document_date: Optional[str] = Field(default=None, description="ISO 8601 date, YYYY-MM-DD")
    effective_date: Optional[str] = Field(default=None)
    expiry_date: Optional[str] = Field(default=None)
    mentioned: list[str] = Field(default_factory=list, max_length=30)


class Identifiers(BaseModel):
    model_config = {"extra": "forbid"}
    ref_numbers: list[str] = Field(default_factory=list, max_length=20)
    amounts: list[str] = Field(default_factory=list, max_length=20)
    currencies: list[str] = Field(default_factory=list, max_length=10)


class DocumentMetadata(BaseModel):
    """The frozen core. Every field is required; absence is expressed as null."""

    model_config = {"extra": "forbid"}

    doc_type: DocType
    title: str = Field(
        description=(
            "The document's title in its ORIGINAL script, copied from the document. "
            "If the document is Arabic, this MUST be Arabic text."
        )
    )
    title_translit: Optional[str] = Field(
        default=None,
        description=(
            "An English/Latin rendering of `title`. If `title` is already Latin "
            "script, repeat it here. This field is ALWAYS Latin script."
        ),
    )
    primary_language: str = Field(description="ISO 639-1, e.g. 'ar' or 'en'")
    languages: list[str] = Field(default_factory=list, max_length=6)
    summary_short: str = Field(max_length=400)
    summary_long: str = Field(default="", max_length=4000)
    keywords: list[str] = Field(default_factory=list, max_length=15)
    topics: list[str] = Field(default_factory=list, max_length=10)
    entities: Entities = Field(default_factory=Entities)
    dates: Dates = Field(default_factory=Dates)
    identifiers: Identifiers = Field(default_factory=Identifiers)

    @field_validator("doc_type", mode="before")
    @classmethod
    def coerce_doc_type(cls, value):
        """Fuzzy-match doc_type rather than failing the whole extraction."""
        if isinstance(value, DocType):
            return value
        if not isinstance(value, str):
            return DocType.OTHER
        normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
        try:
            return DocType(normalized)
        except ValueError:
            pass
        aliases = {
            "agreement": DocType.CONTRACT, "lease": DocType.CONTRACT,
            "bill": DocType.INVOICE, "tax_invoice": DocType.INVOICE,
            "memo": DocType.LETTER, "correspondence": DocType.LETTER,
            "id": DocType.ID_DOCUMENT, "passport": DocType.ID_DOCUMENT,
            "diploma": DocType.CERTIFICATE, "transcript": DocType.ACADEMIC,
            "court": DocType.LEGAL, "ruling": DocType.LEGAL,
            "prescription": DocType.MEDICAL,
        }
        for key, mapped in aliases.items():
            if key in normalized:
                return mapped
        return DocType.OTHER

    @field_validator("primary_language", mode="before")
    @classmethod
    def normalize_language(cls, value):
        if not isinstance(value, str) or not value.strip():
            return "en"
        code = value.strip().lower()[:3]
        return {"ara": "ar", "eng": "en", "arabic": "ar", "english": "en"}.get(code, code[:2])

    @field_validator("languages", mode="before")
    @classmethod
    def normalize_languages(cls, value):
        if not isinstance(value, list):
            return []
        out, seen = [], set()
        for item in value:
            if not isinstance(item, str):
                continue
            code = {"ara": "ar", "eng": "en", "arabic": "ar", "english": "en"}.get(
                item.strip().lower()[:3], item.strip().lower()[:2]
            )
            if code and code not in seen:
                seen.add(code)
                out.append(code)
        return out

    @field_validator("summary_short", "summary_long", mode="before")
    @classmethod
    def truncate(cls, value, info):
        """Truncate over-long summaries instead of failing the extraction.

        The length cap cannot be expressed in the grammar (see
        GRAMMAR_SAFE_MAX_LENGTH), so the model can overshoot. A slightly long
        summary is not a reason to throw away an otherwise good record.
        """
        if not isinstance(value, str):
            return "" if value is None else str(value)
        limit = 400 if info.field_name == "summary_short" else 4000
        return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"

    @field_validator("keywords", "topics", mode="before")
    @classmethod
    def dedupe(cls, value):
        if not isinstance(value, list):
            return []
        out, seen = [], set()
        for item in value:
            text = str(item).strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                out.append(text)
        return out

    def model_post_init(self, __context) -> None:
        # primary_language must appear in languages, or filtering gets confusing.
        if self.primary_language and self.primary_language not in self.languages:
            self.languages.insert(0, self.primary_language)


JSON_TYPE_MAP: dict[str, dict] = {
    "string": {"type": ["string", "null"]},
    "number": {"type": ["number", "null"]},
    "date": {"type": ["string", "null"], "description": "ISO 8601 date YYYY-MM-DD"},
    "boolean": {"type": ["boolean", "null"]},
    "string_array": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
    "money": {"type": ["string", "null"], "description": "Amount with currency, e.g. 'SAR 12,500'"},
    "person": {"type": ["string", "null"]},
    "org": {"type": ["string", "null"]},
}


def inline_defs(schema: dict) -> dict:
    """Resolve every $ref/$defs into a single flat schema.

    Nested $refs are the most common cause of Ollama grammar-compile failures
    and of pathological slowdowns, so the schema handed to the model is always
    fully inlined.
    """
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 12:
            return node
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                name = ref.split("/")[-1]
                target = defs.get(name)
                if target is None:
                    return {k: v for k, v in node.items() if k != "$ref"}
                merged = resolve(copy.deepcopy(target), depth + 1)
                for key, value in node.items():
                    if key != "$ref":
                        merged[key] = value
                return merged
            return {k: resolve(v, depth + 1) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(item, depth + 1) for item in node]
        return node

    return resolve(schema)


# Ollama compiles JSON Schema to GBNF, and `maxLength` becomes an explicit
# character-repetition rule. Past roughly 1000 the grammar fails to parse with
# "Failed to initialize samplers: failed to parse grammar" — verified empirically
# against qwen3.5:9b (1000 compiles, 2000 does not).
#
# So length limits are stripped from the schema handed to the model and enforced
# by Pydantic on the way back instead, where they truncate rather than fail.
GRAMMAR_SAFE_MAX_LENGTH = 1000


def _strip_large_lengths(schema: Any) -> Any:
    if isinstance(schema, dict):
        if schema.get("maxLength", 0) > GRAMMAR_SAFE_MAX_LENGTH:
            schema.pop("maxLength")
        for value in schema.values():
            _strip_large_lengths(value)
    elif isinstance(schema, list):
        for item in schema:
            _strip_large_lengths(item)
    return schema


def _force_all_required(schema: dict) -> dict:
    """Make every property required.

    Pydantic marks defaulted fields optional, and optional fields are exactly
    where a constrained model drifts — it simply omits the hard ones. Requiring
    every key (with null allowed) forces the model to consider each field.
    """
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "properties" in schema:
            schema["required"] = list(schema["properties"].keys())
            schema["additionalProperties"] = False
            for value in schema["properties"].values():
                _force_all_required(value)
        for key in ("items", "anyOf", "oneOf"):
            if key in schema:
                target = schema[key]
                if isinstance(target, list):
                    for item in target:
                        _force_all_required(item)
                else:
                    _force_all_required(target)
    return schema


def core_schema() -> dict:
    return _strip_large_lengths(
        _force_all_required(inline_defs(DocumentMetadata.model_json_schema()))
    )


def build_extraction_schema(accepted_fields: list[dict]) -> dict:
    """Core schema + a custom_fields object closed to exactly these keys.

    This is the trick that makes the "open" part of the metadata reliable: at
    generation time custom_fields is a concrete, typed, closed object, so the
    grammar can constrain it like any other field.
    """
    schema = core_schema()
    if not accepted_fields:
        return schema

    properties = {}
    for field in accepted_fields:
        key = field.get("key")
        if not key:
            continue
        spec = copy.deepcopy(JSON_TYPE_MAP.get(field.get("type", "string"), JSON_TYPE_MAP["string"]))
        label = field.get("label_en") or key
        spec["description"] = f"{label}. {field.get('why', '')}".strip()
        properties[key] = spec

    if properties:
        schema["properties"]["custom_fields"] = {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties.keys()),
            "properties": properties,
        }
        schema["required"] = list(schema["properties"].keys())
    return schema


# ---- Extraction planning ("what can be extracted from this document?") ------


class ProposedField(BaseModel):
    model_config = {"extra": "forbid"}
    key: str = Field(description="snake_case identifier")
    label_en: str
    label_ar: str
    type: str = Field(description="one of: string, number, date, boolean, string_array, money, person, org")
    why: str = Field(max_length=200, description="why this document supports this field")
    example_value: str = Field(description="a value actually visible in the text")
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("key", mode="before")
    @classmethod
    def snake(cls, value):
        import re

        text = str(value).strip().lower()
        text = re.sub(r"[^\w]+", "_", text)
        return re.sub(r"_+", "_", text).strip("_")[:48]

    @field_validator("type", mode="before")
    @classmethod
    def known_type(cls, value):
        text = str(value).strip().lower()
        return text if text in JSON_TYPE_MAP else "string"


class ExtractionPlanOut(BaseModel):
    model_config = {"extra": "forbid"}
    detected_doc_type: str
    proposed_fields: list[ProposedField] = Field(default_factory=list, max_length=15)


def plan_schema() -> dict:
    return _strip_large_lengths(
        _force_all_required(inline_defs(ExtractionPlanOut.model_json_schema()))
    )
