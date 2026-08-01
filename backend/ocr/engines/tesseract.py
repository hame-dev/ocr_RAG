"""Tesseract.

The dependable baseline: fast, CPU-only, works on arm64, and it is the only
Phase 1 engine that reports per-word bounding boxes AND per-word confidence.
That makes it the data source for the bbox overlay and the confidence bars even
when the user prefers another engine's text.
"""
from __future__ import annotations

import logging
import time

from .base import (
    EngineHealth,
    BBox,
    OCRJobInput,
    OCRLine,
    OCREngine,
    OCRPage,
    OCRResult,
    OCRWord,
    PageCallback,
)
from .registry import register

logger = logging.getLogger(__name__)

# Tesseract wants ISO-639-2 codes joined by '+'.
LANG_MAP = {"ar": "ara", "en": "eng", "ara": "ara", "eng": "eng"}


@register
class TesseractEngine(OCREngine):
    name = "tesseract"
    display_name_en = "Tesseract"
    display_name_ar = "تيسراكت"
    description_en = "Classical OCR. Fast and reliable, with word boxes and confidence scores."
    description_ar = "تعرف ضوئي تقليدي. سريع وموثوق، مع مربعات الكلمات ودرجات الثقة."
    tier = "classical"
    supported_languages = {"ara", "eng"}
    supports_boxes = True
    supports_confidence = True
    preferred_profile = "classical"
    preferred_dpi = 300
    est_seconds_per_page = 3.0
    default_timeout_s = 180
    queue = "ocr_cpu"

    def health(self) -> EngineHealth:
        try:
            import pytesseract
        except Exception as exc:
            return EngineHealth(available=False, detail=f"pytesseract not importable: {exc}")

        try:
            version = str(pytesseract.get_tesseract_version())
        except Exception as exc:
            return EngineHealth(available=False, detail=f"tesseract binary missing: {exc}")

        try:
            langs = set(pytesseract.get_languages(config=""))
        except Exception as exc:
            return EngineHealth(
                available=False, version=version, detail=f"cannot list languages: {exc}"
            )

        # An installed Tesseract without the Arabic pack would silently return
        # garbage on Arabic pages, so this is a hard gate rather than a warning.
        missing = {"ara", "eng"} - langs
        if missing:
            return EngineHealth(
                available=False,
                version=version,
                detail=f"missing language data: {', '.join(sorted(missing))}",
            )

        return EngineHealth(
            available=True, version=version, detail=f"{len(langs)} language packs installed"
        )

    def _lang_string(self, languages: list[str]) -> str:
        codes = [LANG_MAP.get(lang, lang) for lang in languages]
        seen, ordered = set(), []
        for code in codes:
            if code not in seen:
                seen.add(code)
                ordered.append(code)
        return "+".join(ordered) or "eng"

    def run(self, job: OCRJobInput, on_page: PageCallback | None = None) -> OCRResult:
        import pytesseract
        from PIL import Image

        lang = self._lang_string(job.languages)
        # psm 3 = fully automatic page segmentation. oem 1 = LSTM only, which is
        # substantially better than the legacy engine on Arabic.
        config = job.options.get("tesseract_config", "--oem 1 --psm 3")

        started = time.monotonic()
        pages: list[OCRPage] = []

        for page_image in job.page_images:
            page_started = time.monotonic()
            image = Image.open(page_image.path)
            width, height = image.size

            data = pytesseract.image_to_data(
                image, lang=lang, config=config, output_type=pytesseract.Output.DICT
            )

            lines, confidences = self._collect_lines(data, width, height)
            text = "\n".join(line.text for line in lines if line.text.strip())

            page = OCRPage(
                page_number=page_image.page_number,
                text=text,
                lines=lines,
                confidence=(sum(confidences) / len(confidences)) if confidences else None,
                width_px=width,
                height_px=height,
                duration_ms=int((time.monotonic() - page_started) * 1000),
            )
            pages.append(page)
            if on_page:
                on_page(page)

        return OCRResult(
            engine=self.name,
            engine_version=str(pytesseract.get_tesseract_version()),
            model_id=lang,
            pages=pages,
            duration_ms=int((time.monotonic() - started) * 1000),
            languages_requested=job.languages,
        )

    def _collect_lines(self, data: dict, width: int, height: int):
        """Group Tesseract's flat word rows into lines with boxes."""
        grouped: dict[tuple, list[int]] = {}
        for i, level in enumerate(data["level"]):
            if level != 5:  # 5 = word
                continue
            if not data["text"][i].strip():
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            grouped.setdefault(key, []).append(i)

        lines: list[OCRLine] = []
        all_conf: list[float] = []

        for key in sorted(grouped):
            indices = grouped[key]
            words: list[OCRWord] = []
            confs: list[float] = []
            xs0, ys0, xs1, ys1 = [], [], [], []

            for i in indices:
                conf = float(data["conf"][i])
                if conf < 0:  # -1 means "no confidence available"
                    conf = 0.0
                confs.append(conf / 100.0)
                x, y = data["left"][i], data["top"][i]
                w, h = data["width"][i], data["height"][i]
                xs0.append(x); ys0.append(y); xs1.append(x + w); ys1.append(y + h)
                words.append(
                    OCRWord(
                        text=data["text"][i],
                        bbox=BBox(x / width, y / height, (x + w) / width, (y + h) / height),
                        confidence=conf / 100.0,
                    )
                )

            if not words:
                continue

            # Tesseract emits words in reading order already, including RTL.
            text = " ".join(w.text for w in words)
            from common.arabic import arabic_char_ratio

            lines.append(
                OCRLine(
                    text=text,
                    bbox=BBox(
                        min(xs0) / width, min(ys0) / height,
                        max(xs1) / width, max(ys1) / height,
                    ),
                    confidence=sum(confs) / len(confs),
                    direction="rtl" if arabic_char_ratio(text) > 0.4 else "ltr",
                    words=words,
                )
            )
            all_conf.extend(confs)

        return lines, all_conf
