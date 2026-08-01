"""Anti-hallucination guards for AI text correction.

This is the difference between a trustworthy product and one that silently
invents content. The model is asked for span replacements only, but a
constrained model can still propose a *plausible* replacement that isn't what
the page says. Every proposal is checked mechanically here, and every rejection
is recorded with its rule name so the guards are auditable rather than a black
box.

Rules are ordered cheapest-first.
"""
from __future__ import annotations

import logging

import regex
from rapidfuzz.distance import Levenshtein

from correction.consensus import overlaps_locked

logger = logging.getLogger(__name__)

MAX_EXPANSION_RATIO = 2.5
MAX_EXPANSION_SLACK = 10
MAX_DISSIMILARITY = 0.6
PAGE_REWRITE_RATIO = 0.25

# Auto-checked in the UI: these categories are mechanical fixes, not rewrites.
SAFE_KINDS = {"spacing", "letterform", "dots", "diacritic"}
AUTO_ACCEPT_CONFIDENCE = 0.8

_DIGITS = regex.compile(r"\d")


def _script_set(text: str) -> set[str]:
    """Which writing systems appear in this text."""
    scripts = set()
    for char in text:
        if regex.match(r"[؀-ۿﭐ-﻿]", char):
            scripts.add("arabic")
        elif regex.match(r"[A-Za-z]", char):
            scripts.add("latin")
        elif char.isdigit():
            scripts.add("digit")
    return scripts


def apply_guards(
    page_text: str,
    changes: list[dict],
    *,
    locked: list[list[int]] | None = None,
    other_engine_texts: list[str] | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Filter proposed changes.

    Returns (accepted, rejected, report). Rejected entries carry `rule`.
    """
    locked = locked or []
    other_engine_texts = other_engine_texts or []
    other_digits = set()
    for text in other_engine_texts:
        other_digits.update(_DIGITS.findall(text))

    accepted: list[dict] = []
    rejected: list[dict] = []
    counts: dict[str, int] = {}

    def reject(change, rule, detail=""):
        counts[rule] = counts.get(rule, 0) + 1
        rejected.append({**change, "rule": rule, "rule_detail": detail})

    for change in changes:
        start = change.get("span_start")
        end = change.get("span_end")
        original = change.get("original", "")
        replacement = change.get("replacement", "")

        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
            reject(change, "invalid_span")
            continue

        # 1. The span must actually say what the model claims it says. This
        #    single check kills most misaligned edits.
        if page_text[start:end] != original:
            reject(
                change, "span_mismatch",
                f"page has {page_text[start:end][:40]!r}, model claimed {original[:40]!r}",
            )
            continue

        if original == replacement:
            reject(change, "no_op")
            continue

        # 2. Every engine already agreed here, so the model is almost certainly
        #    wrong and the engines right.
        if overlaps_locked(start, end, locked):
            reject(change, "consensus_locked", "all engines agreed on this span")
            continue

        # 3. A replacement much longer than the original is added content, not a
        #    correction.
        if len(replacement) > MAX_EXPANSION_RATIO * len(original) + MAX_EXPANSION_SLACK:
            reject(change, "expansion", f"{len(original)} -> {len(replacement)} chars")
            continue

        # 4. A correction should look like the original. A wholly different
        #    string is a rewrite.
        if original:
            distance = Levenshtein.distance(original, replacement)
            if distance / max(len(original), len(replacement), 1) > MAX_DISSIMILARITY:
                reject(change, "too_dissimilar", f"normalized distance {distance}")
                continue

        # 5. Introducing a script that was not in the original means translation
        #    or transliteration, both explicitly forbidden.
        new_scripts = _script_set(replacement) - _script_set(original) - {"digit"}
        if new_scripts:
            reject(change, "script_switch", f"introduced {sorted(new_scripts)}")
            continue

        # 6. Numbers are the most expensive hallucination in this product: a
        #    wrong amount on an invoice is worse than no answer. A digit may
        #    only appear if it was in the original, or if some other engine also
        #    read that digit on this page.
        invented = {
            d for d in _DIGITS.findall(replacement)
            if d not in _DIGITS.findall(original) and d not in other_digits
        }
        if invented:
            reject(change, "digit_invented", f"digits {sorted(invented)} appear nowhere")
            continue

        accepted.append(change)

    # 7. Overlapping edits cannot both be applied; keep the more confident one.
    accepted.sort(key=lambda c: (-c.get("confidence", 0), c["span_start"]))
    kept: list[dict] = []
    for change in accepted:
        if any(
            change["span_start"] < k["span_end"] and change["span_end"] > k["span_start"]
            for k in kept
        ):
            reject(change, "overlap", "conflicts with a higher-confidence change")
            continue
        kept.append(change)
    kept.sort(key=lambda c: c["span_start"])

    # 8. Not a rejection: a page where a quarter of the characters change is
    #    flagged for human review rather than silently applied.
    touched = sum(c["span_end"] - c["span_start"] for c in kept)
    needs_review = bool(page_text) and touched / max(len(page_text), 1) > PAGE_REWRITE_RATIO

    for change in kept:
        change["auto_accept"] = (
            change.get("kind") in SAFE_KINDS
            and change.get("confidence", 0) >= AUTO_ACCEPT_CONFIDENCE
            and not needs_review
        )

    report = {
        "proposed": len(changes),
        "accepted": len(kept),
        "rejected": len(rejected),
        "rejections_by_rule": counts,
        "chars_touched": touched,
        "needs_human_review": needs_review,
    }
    return kept, rejected, report


def apply_changes(page_text: str, changes: list[dict]) -> str:
    """Apply accepted changes. Right-to-left so earlier offsets stay valid."""
    out = page_text
    for change in sorted(changes, key=lambda c: c["span_start"], reverse=True):
        out = out[: change["span_start"]] + change["replacement"] + out[change["span_end"] :]
    return out
