"""Diffing that is correct for Arabic.

Everything here operates on grapheme clusters. Splitting Arabic on code points
detaches combining marks from their base letters and produces diffs that look
like noise; splitting words on \\s+ is wrong for the same reason.
"""
from __future__ import annotations

import difflib

from .arabic import graphemes


def grapheme_opcodes(a: str, b: str) -> list[tuple]:
    """SequenceMatcher opcodes over grapheme clusters, remapped to char offsets."""
    ga, gb = graphemes(a), graphemes(b)
    matcher = difflib.SequenceMatcher(None, ga, gb, autojunk=False)

    # Grapheme index -> character offset, so callers can slice the original text.
    off_a = _offsets(ga)
    off_b = _offsets(gb)

    ops = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        ops.append((tag, off_a[i1], off_a[i2], off_b[j1], off_b[j2]))
    return ops


def _offsets(items: list[str]) -> list[int]:
    out, acc = [0], 0
    for item in items:
        acc += len(item)
        out.append(acc)
    return out


def diff_stats(a: str, b: str) -> dict:
    """Summarize a revision against its parent."""
    ga, gb = graphemes(a), graphemes(b)
    matcher = difflib.SequenceMatcher(None, ga, gb, autojunk=False)
    added = removed = changed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            changed += max(i2 - i1, j2 - j1)
    total = max(len(ga), 1)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "changed_ratio": round((added + removed + changed) / total, 4),
        "similarity": round(matcher.ratio(), 4),
        "edited_pages": _edited_pages(a, b),
    }


def _edited_pages(a: str, b: str) -> list[int]:
    """Which 1-based pages differ. Pages are separated by \\f everywhere."""
    pa, pb = a.split("\f"), b.split("\f")
    return [i + 1 for i in range(max(len(pa), len(pb)))
            if (pa[i] if i < len(pa) else None) != (pb[i] if i < len(pb) else None)]


def changed_ratio(a: str, b: str) -> float:
    return diff_stats(a, b)["changed_ratio"]
