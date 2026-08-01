"""Arabic text handling.

`ar_normalize` MUST stay behaviourally identical to the SQL function
`ar_normalize_v1` in rag/migrations. A test asserts they agree over a shared
table — if they drift, search silently degrades and nothing else tells you.
"""
from __future__ import annotations

import unicodedata

import regex

# Tashkeel (harakat) + superscript alef + tatweel. These carry no lexical weight
# in search and their presence is inconsistent in OCR output, so recall dies
# without stripping them.
_STRIP = regex.compile(r"[ً-ٰٟۖ-ۭـ]")

# Orthographic folding. These variants are the dominant recall killers in Arabic
# search: writers and OCR engines disagree constantly about hamza placement,
# ta-marbuta vs ha, and alef-maqsura vs ya.
_FOLD = str.maketrans(
    {
        "أ": "ا",  # أ -> ا
        "إ": "ا",  # إ -> ا
        "آ": "ا",  # آ -> ا
        "ٱ": "ا",  # ٱ -> ا
        "ى": "ي",  # ى -> ي
        "ة": "ه",  # ة -> ه
        "ؤ": "و",  # ؤ -> و
        "ئ": "ي",  # ئ -> ي
        # Arabic-Indic and Extended Arabic-Indic digits -> ASCII
        **{chr(0x0660 + i): str(i) for i in range(10)},
        **{chr(0x06F0 + i): str(i) for i in range(10)},
    }
)

_ARABIC_LETTER = regex.compile(r"[ء-غف-ي]")
_ARABIC_ANY = regex.compile(r"[؀-ۿﭐ-﷿ﹰ-﻿]")
# Presentation forms: Arabic that has been baked into positional glyphs. Their
# presence in extracted PDF text is the tell-tale of a bad text layer.
_PRESENTATION = regex.compile(r"[ﭐ-﷿ﹰ-﻿]")
# Final-form glyphs specifically; a *run* starting with one implies visual order.
_FINAL_FORMS = regex.compile(r"[ﺂﺄﺈﺊﺎﺐﺔﺖﺚﺞﺢﺦﺪﺬﺮﺰﺲﺶﺺﺾﻂﻆﻊﻎﻒﻖﻚﻞﻢﻦﻪﻮﻰﻲ]")


def ar_normalize(text: str) -> str:
    """Fold Arabic orthography for search. Mirror of SQL ar_normalize_v1."""
    if not text:
        return ""
    return _STRIP.sub("", text).translate(_FOLD).lower()


def nfkc(text: str) -> str:
    """Collapse presentation forms back to base letters."""
    return unicodedata.normalize("NFKC", text or "")


def arabic_char_ratio(text: str) -> float:
    """Share of letter characters that are Arabic. Sanity metric on OCR output."""
    if not text:
        return 0.0
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _ARABIC_ANY.match(c)) / len(letters)


def has_presentation_forms(text: str) -> bool:
    return bool(_PRESENTATION.search(text or ""))


def detect_lang(text: str) -> str:
    """Per-chunk language tag used for filtering."""
    ratio = arabic_char_ratio(text)
    if ratio > 0.6:
        return "ar"
    if ratio < 0.15:
        return "en"
    return "mixed"


def bidi_suspect(text: str) -> tuple[bool, dict]:
    """Detect Arabic that is stored in visual rather than logical order.

    Arabic in PDFs is frequently emitted as presentation forms, and often
    reversed. Such text *renders* fine but is unusable: search, chunking and the
    LLM all see scrambled words. When this fires we ignore the embedded text
    layer and OCR the page instead.

    Heuristic: in correct logical order a run of Arabic almost never *starts*
    with a final-form glyph, because final forms terminate words. A high rate of
    runs beginning with one means the string was laid out visually.
    """
    if not text:
        return False, {"reason": "empty"}

    runs = regex.findall(r"[؀-ۿﭐ-﷿ﹰ-﻿]+", text)
    if not runs:
        return False, {"reason": "no_arabic"}

    presentation = sum(1 for r in runs if _PRESENTATION.search(r))
    starts_final = sum(1 for r in runs if _FINAL_FORMS.match(r))
    ratio = starts_final / len(runs)

    report = {
        "runs": len(runs),
        "runs_with_presentation_forms": presentation,
        "runs_starting_with_final_form": starts_final,
        "final_form_start_ratio": round(ratio, 4),
    }
    # >15% is far above what correctly-ordered text produces.
    return ratio > 0.15, report


def graphemes(text: str) -> list[str]:
    r"""Split into grapheme clusters.

    Diffing Arabic by code point splits combining marks off their base letters
    and produces nonsense diffs, so every diff in this system goes through \X.
    """
    return regex.findall(r"\X", text or "")


def gibberish_score(text: str) -> float:
    """Cheap, model-free OCR quality signal in [0, 1]; higher is worse.

    Used to rank the comparison grid and to flag quality issues, so that a bad
    engine result is visible to the user instead of silently winning.
    """
    if not text or not text.strip():
        return 1.0

    n = len(text)
    replacement = sum(1 for c in text if c in "�￾") / n
    control = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t\f") / n

    words = [w for w in regex.split(r"\s+", text) if w]
    if not words:
        return 1.0

    single = sum(1 for w in words if len(w) == 1) / len(words)
    mean_len = sum(len(w) for w in words) / len(words)
    length_penalty = 0.0 if 2 <= mean_len <= 14 else min(1.0, abs(mean_len - 8) / 12)

    # Arabic left as isolated presentation forms means the text layer or engine
    # failed to produce real letters.
    arabic = _ARABIC_ANY.findall(text)
    pres = 0.0
    if arabic:
        pres = len(_PRESENTATION.findall(text)) / len(arabic)

    score = (
        2.0 * replacement
        + 2.0 * control
        + 0.6 * single
        + 0.5 * length_penalty
        + 0.8 * pres
    )
    return max(0.0, min(1.0, score))
