"""Chunking for a bilingual Arabic/English corpus.

The tokenizer matters more here than anywhere else in the system. XLM-RoBERTa
(what bge-m3 uses) averages roughly one token per 2.5-3 Arabic characters versus
~4 for English, so a character-based splitter produces Arabic chunks about 40%
over budget — they then get truncated at embed time and retrieval quietly
degrades. So we use the real tokenizer, with a language-aware heuristic only as
an offline fallback.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import regex

from common.arabic import arabic_char_ratio, detect_lang

logger = logging.getLogger(__name__)

TARGET_TOKENS = 512
OVERLAP_TOKENS = 96
MIN_TOKENS = 64
MAX_TOKENS = 768

# Ordered most- to least-preferred split point. Arabic full stop (۔), question
# mark (؟), semicolon (؛) and comma (،) are distinct code points from their
# Latin equivalents and are missed by every default splitter.
SEPARATORS = ["\n\n", "\n", "۔", ".", "؟", "?", "!", "؛", ";", "،", ",", " "]

_tokenizer = None
_tokenizer_failed = False


def get_tokenizer():
    global _tokenizer, _tokenizer_failed
    if _tokenizer is not None or _tokenizer_failed:
        return _tokenizer
    try:
        from tokenizers import Tokenizer

        _tokenizer = Tokenizer.from_pretrained("BAAI/bge-m3")
    except Exception:
        logger.warning(
            "bge-m3 tokenizer unavailable; falling back to the length heuristic "
            "(chunk sizes will be approximate)",
            exc_info=True,
        )
        _tokenizer_failed = True
    return _tokenizer


def count_tokens(text: str) -> int:
    tokenizer = get_tokenizer()
    if tokenizer is not None:
        return len(tokenizer.encode(text, add_special_tokens=False).ids)
    return heuristic_tokens(text)


def heuristic_tokens(text: str) -> int:
    """Language-aware fallback. Never as good as the real tokenizer."""
    if not text:
        return 0
    arabic = len(regex.findall(r"[؀-ۿ]", text))
    digits = len(regex.findall(r"\d", text))
    latin = len(text) - arabic - digits
    return int(arabic / 2.7 + latin / 4.0 + digits / 2.0) + 1


@dataclass
class ChunkDraft:
    text: str
    chunk_index: int
    page_start: int
    page_end: int
    section_path: str = ""
    lang: str = "mixed"
    token_count: int = 0
    char_count: int = 0
    meta: dict = field(default_factory=dict)


_HEADING_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)\s]")
# "Page 3 of 12", "3 / 12", "صفحة ٣", "٣ من ١٢", "- 4 -": page furniture, never a heading.
_PAGE_NUMBER = re.compile(
    r"^\s*[-–—.\s]*(page|p\.|الصفحة|صفحة|ص)?\s*[\d٠-٩]+\s*((of|/|من|-)\s*[\d٠-٩]+)?[-–—.\s]*$",
    re.IGNORECASE,
)


def _is_heading(line: str, *, block_lines: int = 2) -> bool:
    """Whether a block's first line reads as a section heading.

    Headings now persist across pages, so a false positive costs more than it
    used to: a page number would become the section of the rest of the
    document. Hence the extra rejections below.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return False
    if _PAGE_NUMBER.match(stripped):
        return False
    # Measured without the leading section number and without spaces, so
    # "3.1.2 Fees" is judged by "Fees", while "12.03.2024 10:45" is rejected.
    number = _HEADING_NUMBER.match(stripped)
    rest = "".join((stripped[number.end():] if number else stripped).split())
    letters = sum(1 for ch in rest if ch.isalpha())
    if letters < 3 or (len(rest) - letters) / len(rest) > 0.4:
        return False
    if _HEADING_NUMBER.match(stripped):
        return True
    if stripped.isupper() and len(stripped) > 3:
        return True
    # A short line with no terminal punctuation reads as a heading in both
    # scripts — but only when something follows it in the same block; a lone
    # line (a caption, a stamp) is not the section for everything after it.
    return (
        block_lines >= 2
        and len(stripped) < 60
        and not stripped.endswith((".", "،", "۔", ":", ";"))
    )


def _split_recursive(text: str, max_tokens: int) -> list[str]:
    """Split on the most semantic separator that gets us under budget."""
    if count_tokens(text) <= max_tokens:
        return [text]

    for separator in SEPARATORS:
        if separator not in text:
            continue
        parts = text.split(separator)
        if len(parts) < 2:
            continue

        chunks, current = [], ""
        for part in parts:
            candidate = f"{current}{separator}{part}" if current else part
            if count_tokens(candidate) > max_tokens and current:
                chunks.append(current)
                current = part
            else:
                current = candidate
        if current:
            chunks.append(current)

        out = []
        for chunk in chunks:
            out.extend(_split_recursive(chunk, max_tokens) if count_tokens(chunk) > max_tokens else [chunk])
        return out

    # No separator left: hard-cut on characters as a last resort.
    approx = max(1, len(text) * max_tokens // max(count_tokens(text), 1))
    return [text[i : i + approx] for i in range(0, len(text), approx)]


def _overlap_tail(text: str, tokens: int) -> str:
    """Take roughly `tokens` worth of trailing text, on a word boundary."""
    if tokens <= 0:
        return ""
    words = text.split()
    if not words:
        return ""
    tail: list[str] = []
    for word in reversed(words):
        tail.insert(0, word)
        if count_tokens(" ".join(tail)) >= tokens:
            break
    return " ".join(tail)


def chunk_text(
    text: str,
    *,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    min_tokens: int = MIN_TOKENS,
    max_tokens: int = MAX_TOKENS,
) -> list[ChunkDraft]:
    """Chunk a finalized revision.

    Pages are a HARD boundary: a chunk never spans a page break unless the
    trailing fragment is too small to stand alone, and the overlap tail is
    never carried across one. Page-accurate citations are worth more than
    perfectly even chunk sizes — the user has to be able to click a citation
    and land on the right page image.
    """
    drafts: list[ChunkDraft] = []
    pages = text.split("\f")
    index = 0
    # `carry` is text prepended to the next block. Two kinds, treated
    # differently at a page break: an "overlap" carry (the tail of the previous
    # chunk, for continuity) is dropped so the first chunk of a page starts on
    # that page; a "fragment" carry (a block too small to stand alone) is kept
    # and remembers the page it came from.
    carry = ""
    carry_kind = ""
    carry_page = 1
    # The section heading persists across pages until a new heading appears.
    section_path = ""

    for page_offset, page_text in enumerate(pages):
        page_number = page_offset + 1
        if not page_text.strip():
            continue
        if carry_kind == "overlap":
            carry, carry_kind = "", ""

        blocks = _split_recursive(page_text, max_tokens)

        for block in blocks:
            if not block.strip():
                continue

            lines = block.strip().split("\n")
            if _is_heading(lines[0], block_lines=len(lines)):
                section_path = lines[0].strip()[:120]

            body = f"{carry}\n{block}" if carry else block
            body_page = carry_page if carry_kind == "fragment" else page_number
            carry, carry_kind = "", ""
            tokens = count_tokens(body)

            if tokens < min_tokens:
                # Too small to stand alone: carry it into the next block, even
                # across a page break.
                carry, carry_kind, carry_page = body, "fragment", body_page
                continue

            drafts.append(
                ChunkDraft(
                    text=body.strip(),
                    chunk_index=index,
                    page_start=min(body_page, page_number),
                    page_end=page_number,
                    section_path=section_path,
                    lang=detect_lang(body),
                    token_count=tokens,
                    char_count=len(body),
                    meta={"arabic_ratio": round(arabic_char_ratio(body), 3)},
                )
            )
            index += 1

            if overlap_tokens > 0:
                carry, carry_kind, carry_page = _overlap_tail(body, overlap_tokens), "overlap", page_number

    # Only a genuine fragment becomes a tail chunk; an overlap carry is text
    # that already lives in the previous chunk.
    if carry_kind == "fragment" and carry.strip() and count_tokens(carry) >= 16:
        drafts.append(
            ChunkDraft(
                text=carry.strip(),
                chunk_index=index,
                page_start=carry_page,
                page_end=len(pages),
                section_path=section_path,
                lang=detect_lang(carry),
                token_count=count_tokens(carry),
                char_count=len(carry),
                meta={"arabic_ratio": round(arabic_char_ratio(carry), 3)},
            )
        )

    return drafts

