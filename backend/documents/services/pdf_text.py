"""Digital-text detection: decide whether a PDF's embedded text layer is usable.

This runs BEFORE any rasterization. If a PDF already carries good text we skip
OCR entirely — it is faster and strictly more accurate.

The Arabic case is the subtle one. Arabic in PDFs frequently arrives as
presentation forms (U+FE70-FEFF) and often in visual rather than logical order.
Such text *renders* correctly in a viewer but is unusable downstream: search,
chunking and the LLM all see scrambled words. So a text layer is only trusted
after it passes a bidi sanity check; otherwise we treat the PDF as scanned.
"""
from __future__ import annotations

import logging

import pypdfium2 as pdfium

from common.arabic import arabic_char_ratio, bidi_suspect, has_presentation_forms, nfkc

logger = logging.getLogger(__name__)

# A page needs this many characters before it counts as "has text".
MIN_CHARS_PER_PAGE = 120
# Share of pages that must have text for the document to count as digital.
MIN_PAGE_COVERAGE = 0.60
# Guards against pages that are mostly punctuation/artefacts.
MIN_ALNUM_RATIO = 0.50


def extract_page_text(page) -> str:
    textpage = page.get_textpage()
    try:
        return textpage.get_text_range() or ""
    finally:
        textpage.close()


def analyze_pdf(path: str) -> dict:
    """Return a verdict on whether this PDF's text layer should be trusted."""
    report: dict = {
        "pages": [],
        "page_count": 0,
        "is_digital": False,
        "bidi_suspect": False,
        "reason": "",
    }

    try:
        pdf = pdfium.PdfDocument(path)
    except Exception as exc:
        report["reason"] = f"not_a_pdf: {exc}"
        return report

    try:
        report["page_count"] = len(pdf)
        pages_with_text = 0
        any_bidi_suspect = False

        for i in range(len(pdf)):
            page = pdf[i]
            raw = extract_page_text(page)
            # NFKC collapses presentation forms back to base letters.
            text = nfkc(raw)
            chars = len(text.strip())
            alnum = sum(1 for c in text if c.isalnum())
            alnum_ratio = (alnum / chars) if chars else 0.0

            suspect, bidi_report = bidi_suspect(raw)
            if suspect:
                any_bidi_suspect = True

            has_text = chars >= MIN_CHARS_PER_PAGE and alnum_ratio >= MIN_ALNUM_RATIO
            if has_text:
                pages_with_text += 1

            report["pages"].append(
                {
                    "page_number": i + 1,
                    "char_count": chars,
                    "alnum_ratio": round(alnum_ratio, 3),
                    "arabic_ratio": round(arabic_char_ratio(text), 3),
                    "presentation_forms": has_presentation_forms(raw),
                    "bidi": bidi_report,
                    "has_text": has_text,
                    "text": text,
                }
            )
            page.close()

        coverage = pages_with_text / max(len(pdf), 1)
        report["coverage"] = round(coverage, 3)
        report["bidi_suspect"] = any_bidi_suspect

        if coverage < MIN_PAGE_COVERAGE:
            report["reason"] = f"only {coverage:.0%} of pages carry text; treating as scanned"
        elif any_bidi_suspect:
            # This is the important branch: the text layer looks fine to a human
            # but is stored in visual order, so OCR will beat it.
            report["reason"] = (
                "embedded Arabic text appears to be in visual order or "
                "presentation forms; OCR will be used instead"
            )
        else:
            report["is_digital"] = True
            report["reason"] = "usable embedded text layer"

        return report
    finally:
        pdf.close()


def page_texts(report: dict) -> list[str]:
    return [p["text"] for p in report.get("pages", [])]
