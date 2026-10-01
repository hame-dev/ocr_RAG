"""Per-chunk keywords without an LLM.

YAKE is statistical and language-independent apart from a stopword list, which
it ships for Arabic and English. The keywords go into the weight-A lexical
column, NOT into the embedding: entity-heavy prefixes hurt dense retrieval, but
an exact-term match on a keyword is exactly what lexical search is for.

Trap: when YAKE cannot find a language's stopword file it silently falls back
to a language-agnostic list, and function words start showing up as keywords.
A test pins that "في", "من", "the" never appear.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

TOP_KEYWORDS = 8
_ARABIC = re.compile(r"[؀-ۿ]")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

# YAKE's Arabic list is short; these are the function words that still leak.
AR_STOPWORDS_EXTRA = {
    "في", "من", "على", "إلى", "الى", "عن", "أن", "ان", "إن", "أو", "او", "و", "ثم", "لا", "ما",
    "هذا", "هذه", "ذلك", "تلك", "التي", "الذي", "الذين", "كل", "بعض", "غير", "بين", "حتى",
    "قد", "لم", "لن", "كان", "كانت", "يكون", "هو", "هي", "هم", "مع", "عند", "بعد", "قبل",
    "منذ", "حيث", "كما", "إذا", "اذا", "لكن", "ولكن", "بل", "أي", "اي", "هنا", "هناك",
    "به", "بها", "له", "لها", "فيه", "فيها", "منه", "منها", "عليه", "عليها", "إليه", "اليه",
    "ذات", "ذو", "أما", "اما", "فقط", "أيضا", "ايضا", "بشرط", "بمبلغ", "خلال", "ضمن", "وفق",
}
EN_STOPWORDS_EXTRA = {"the", "of", "and", "is", "at", "in", "to", "for", "by", "an", "a", "or"}

_extractors: dict[str, object] = {}


def _extractor(lang: str, top: int):
    key = f"{lang}:{top}"
    if key not in _extractors:
        import yake

        _extractors[key] = yake.KeywordExtractor(
            lan=lang, n=2, dedupLim=0.9, dedupFunc="seqm", windowsSize=1, top=top * 3
        )
    return _extractors[key]


def _clean(keyword: str, lang: str) -> str | None:
    keyword = " ".join(keyword.split()).strip("-–—.,،؛;:!?؟\"'()[]{}")
    if len(keyword) < 3 or len(_LETTER.findall(keyword)) < 3:
        return None
    stop = AR_STOPWORDS_EXTRA if lang == "ar" else EN_STOPWORDS_EXTRA
    words = [w for w in keyword.split() if w.lower() not in stop]
    if not words:
        return None
    keyword = " ".join(words)
    return keyword if len(_LETTER.findall(keyword)) >= 3 else None


def _scored(text: str, lang: str, top: int) -> list[tuple[float, str]]:
    try:
        pairs = _extractor(lang, top).extract_keywords(text)
    except ImportError:
        # An image without yake must fail the index task, not quietly produce
        # a library with no keywords (which --only-missing would then skip).
        raise
    except Exception:
        logger.warning("keyword extraction failed (lang=%s)", lang, exc_info=True)
        return []
    out: list[tuple[float, str]] = []
    seen: set[str] = set()
    for keyword, score in pairs:
        cleaned = _clean(keyword, lang)
        if cleaned is None or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        out.append((float(score), cleaned))  # lower is better in YAKE
    return out


def extract_keywords(text: str, lang: str, top: int = TOP_KEYWORDS) -> list[str]:
    """Up to `top` keywords/keyphrases for one chunk. `lang` is ar, en or mixed."""
    if not text or not text.strip():
        return []
    if lang == "mixed":
        arabic = "\n".join(line for line in text.splitlines() if _ARABIC.search(line))
        latin = "\n".join(line for line in text.splitlines() if not _ARABIC.search(line))
        scored = _scored(arabic, "ar", top) + _scored(latin, "en", top)
        scored.sort(key=lambda pair: pair[0])
    else:
        scored = _scored(text, "ar" if lang == "ar" else "en", top)
    return [keyword for _, keyword in scored[:top]]


def keywords_text(keywords: list[str]) -> str:
    """The keywords as stored in the weight-A lexical column."""
    return "; ".join(k for k in keywords if k)
