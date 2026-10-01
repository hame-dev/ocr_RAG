"""How OCR runs of one batch are ranked against each other."""
from __future__ import annotations

from ocr.models import OCRRun


def rank_key(run: OCRRun) -> float:
    """Rank engine outputs for the comparison grid (and for picking a baseline).

    Confidence is not comparable across engines and several report none at all,
    so the model-free gibberish signal carries most of the weight.
    """
    confidence = run.mean_confidence if run.mean_confidence is not None else 0.5
    gibberish = run.gibberish_score if run.gibberish_score is not None else 0.5
    penalty = 0.3 if "suspected_summarization" in (run.warnings or []) else 0.0
    return 0.35 * confidence + 0.65 * (1.0 - gibberish) - penalty
