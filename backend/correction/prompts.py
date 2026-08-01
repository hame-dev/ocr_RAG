"""Correction prompt + output grammar.

The output schema is the enforcement mechanism, not the prompt: the model
literally cannot emit a new paragraph, because the grammar only permits a list
of span replacements. The prompt explains intent; the grammar makes it binding.
"""

SYSTEM = (
    "You are a proofreader for OCR output of a scanned document. An image of the "
    "page is provided alongside the text. You may ONLY fix characters that the OCR "
    "misrecognized, and only where the image clearly shows a different character. "
    "You must NOT add, remove, translate, summarize, rephrase, reorder or complete "
    "any content. You must NOT fix grammar or spelling that is genuinely present in "
    "the original document — reproduce the document, do not improve it. If a region "
    "is illegible, leave it unchanged and use kind='illegible'. If nothing is wrong, "
    "return an empty corrections array. Preserve the original language and script of "
    "every span; never transliterate."
)

USER = """Page {page_number} of {page_count}.

Below is the OCR text with character offsets. Compare it against the page image \
and report ONLY misread characters.

For each correction:
- `span_start` and `span_end` are character offsets into the text below.
- `original` MUST exactly equal text[span_start:span_end].
- `replacement` is what the image actually shows.
- Keep spans short — a word or two, not a sentence.

OCR TEXT:
{text}"""

# Grammar-constrained output. maxLength stays well under the limit where
# Ollama's GBNF conversion starts failing (see enrichment/schemas.py).
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["page_is_illegible", "corrections"],
    "properties": {
        "page_is_illegible": {"type": "boolean"},
        "corrections": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "span_start", "span_end", "original", "replacement",
                    "kind", "reason", "confidence",
                ],
                "properties": {
                    "span_start": {"type": "integer", "minimum": 0},
                    "span_end": {"type": "integer", "minimum": 0},
                    "original": {"type": "string", "maxLength": 200},
                    "replacement": {"type": "string", "maxLength": 200},
                    "kind": {
                        "enum": [
                            "letterform", "diacritic", "dots", "spacing",
                            "joined_word", "split_word", "digit", "punctuation",
                            "line_merge", "illegible", "other",
                        ]
                    },
                    "reason": {"type": "string", "maxLength": 200},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
    },
}
