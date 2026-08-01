"""Cross-engine consensus.

Where every engine independently produced the same characters, that text is
almost certainly right. Those regions are:
  1. tinted green in the comparison grid, so the user can skim to the parts that
     actually differ
  2. used as a HARD guard during AI correction — the model is not allowed to
     "fix" text that every engine already agreed on

It costs nothing to compute and it is the highest-signal correctness prior
available without a human, which is why it is worth doing even with 2 engines.
"""
from __future__ import annotations

import difflib

from common.arabic import graphemes


def locked_spans(texts: list[str], min_run: int = 12) -> tuple[list[list[int]], float]:
    """Character spans (into texts[0]) where every text agrees.

    Returns (spans, agreement_ratio). Runs shorter than `min_run` graphemes are
    dropped: short coincidental matches (spaces, single letters) are not
    evidence, and locking them would over-restrict correction.
    """
    non_empty = [t for t in texts if t and t.strip()]
    if len(non_empty) < 2:
        # With one engine there is no consensus to compute; lock nothing so the
        # corrector stays free to work.
        return [], 0.0

    base = graphemes(non_empty[0])
    # Start by assuming every position agrees, then intersect with each engine.
    agreed = [True] * len(base)

    for other in non_empty[1:]:
        other_g = graphemes(other)
        matcher = difflib.SequenceMatcher(None, base, other_g, autojunk=False)
        matched = [False] * len(base)
        for i, _j, size in matcher.get_matching_blocks():
            for k in range(i, min(i + size, len(base))):
                matched[k] = True
        agreed = [a and m for a, m in zip(agreed, matched)]

    # Grapheme index -> character offset.
    offsets, acc = [0], 0
    for cluster in base:
        acc += len(cluster)
        offsets.append(acc)

    spans: list[list[int]] = []
    start = None
    for index, ok in enumerate(agreed + [False]):
        if ok and start is None:
            start = index
        elif not ok and start is not None:
            if index - start >= min_run:
                spans.append([offsets[start], offsets[index]])
            start = None

    agreement = sum(agreed) / max(len(agreed), 1)
    return spans, round(agreement, 4)


def overlaps_locked(start: int, end: int, spans: list[list[int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)
