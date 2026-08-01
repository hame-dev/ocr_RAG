"""Native PDF text layer — the free, perfectly-accurate "engine".

When a PDF already carries usable text there is no reason to OCR it. This is
strictly better than any OCR engine on such files, and it costs milliseconds.

It refuses to run when the text layer failed the bidi sanity check, because
visually-ordered Arabic looks fine and is unusable.
"""
from __future__ import annotations

import time

from .base import (
    EngineHealth,
    EngineNotApplicable,
    OCRJobInput,
    OCREngine,
    OCRPage,
    OCRResult,
    PageCallback,
)
from .registry import register


@register
class NativePDFEngine(OCREngine):
    name = "native_pdf"
    display_name_en = "Embedded PDF text"
    display_name_ar = "النص المضمّن في الملف"
    description_en = "Reads the PDF's own text layer. Instant and exact — no OCR involved."
    description_ar = "يقرأ طبقة النص الأصلية في ملف PDF. فوري ودقيق تماماً بدون تعرف ضوئي."
    tier = "native"
    supported_languages = {"ara", "eng", "any"}
    supports_boxes = False
    supports_confidence = False
    needs_raster = False
    preferred_profile = "raw"
    est_seconds_per_page = 0.02
    default_timeout_s = 60
    queue = "ocr_cpu"

    def health(self) -> EngineHealth:
        try:
            import pypdfium2

            return EngineHealth(
                available=True,
                version=getattr(pypdfium2, "V_PYPDFIUM2", "unknown"),
                detail="Available for PDFs that carry a usable text layer.",
            )
        except Exception as exc:
            return EngineHealth(available=False, detail=f"pypdfium2 missing: {exc}")

    def run(self, job: OCRJobInput, on_page: PageCallback | None = None) -> OCRResult:
        if not job.native_page_texts:
            raise EngineNotApplicable(
                "this document has no usable embedded text layer; use an OCR engine"
            )

        started = time.monotonic()
        pages: list[OCRPage] = []
        for index, text in enumerate(job.native_page_texts):
            page = OCRPage(
                page_number=index + 1,
                text=text or "",
                # Confidence is meaningless here: the text is exact, not inferred.
                confidence=None,
                duration_ms=0,
            )
            pages.append(page)
            if on_page:
                on_page(page)

        return OCRResult(
            engine=self.name,
            engine_version=self.health().version or "",
            model_id=None,
            pages=pages,
            duration_ms=int((time.monotonic() - started) * 1000),
            languages_requested=job.languages,
            warnings=[],
        )
