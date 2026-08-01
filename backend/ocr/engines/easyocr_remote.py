"""EasyOCR, via its sidecar (Phase 2).

EasyOCR lives in its own container because its torch dependency tree is ~1.5GB
and it downloads weights on first use. Isolating it keeps that size and its OOM
behaviour away from the main worker.

The sidecar exchanges FILE PATHS over the shared /data/media volume, never image
bytes — multi-MB PNGs should not cross a socket, and a sidecar crash then shows
up as a connection error in this adapter rather than killing a worker.
"""
from __future__ import annotations

import logging
import time

import httpx
from django.conf import settings

from .base import (
    BBox,
    EngineHealth,
    OCRJobInput,
    OCRLine,
    OCRPage,
    OCRResult,
    OCREngine,
    PageCallback,
    TransientEngineError,
)
from .registry import register

logger = logging.getLogger(__name__)


class RemoteSidecarEngine(OCREngine):
    """Shared client for the HTTP OCR sidecars."""

    endpoint_setting = ""

    @property
    def base_url(self) -> str:
        return getattr(settings, self.endpoint_setting).rstrip("/")

    def health(self) -> EngineHealth:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=5.0)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            return EngineHealth(available=False, detail=f"sidecar unreachable: {exc}")

        # ready=False until weights finish loading, so the UI never offers an
        # engine that would stall the user for 90 seconds on first use.
        if not data.get("ready"):
            return EngineHealth(
                available=False,
                version=data.get("version"),
                detail=data.get("detail", "sidecar still loading models"),
            )
        return EngineHealth(
            available=True,
            version=data.get("version"),
            model_id=",".join(data.get("models_loaded", [])) or None,
            detail="sidecar ready",
        )

    def run(self, job: OCRJobInput, on_page: PageCallback | None = None) -> OCRResult:
        payload = {
            "job_id": job.document_id,
            "pages": [
                {
                    "page_number": p.page_number,
                    "path": p.path,
                    "dpi": p.dpi,
                    "width_px": p.width_px,
                    "height_px": p.height_px,
                }
                for p in job.page_images
            ],
            "languages": job.languages,
            "options": job.options,
            "deadline_s": job.deadline_s,
        }

        started = time.monotonic()
        try:
            response = httpx.post(
                f"{self.base_url}/ocr", json=payload, timeout=job.deadline_s + 30
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            # Any sidecar failure is transient by definition here: it must never
            # surface as anything but a single failed run.
            raise TransientEngineError(f"{self.name} sidecar failed: {exc}") from exc

        pages: list[OCRPage] = []
        for page_data in data.get("pages", []):
            lines = [
                OCRLine(
                    text=line.get("t", ""),
                    bbox=BBox(*line["bbox"]) if line.get("bbox") else None,
                    confidence=line.get("c"),
                    direction=line.get("dir", "unknown"),
                )
                for line in page_data.get("lines", [])
            ]
            page = OCRPage(
                page_number=page_data["page_number"],
                text=page_data.get("text", ""),
                lines=lines,
                confidence=page_data.get("confidence"),
                width_px=page_data.get("width_px", 0),
                height_px=page_data.get("height_px", 0),
                duration_ms=page_data.get("duration_ms", 0),
                warnings=page_data.get("warnings", []),
            )
            pages.append(page)
            if on_page:
                on_page(page)

        return OCRResult(
            engine=self.name,
            engine_version=data.get("engine_version", ""),
            model_id=data.get("model_id"),
            pages=pages,
            duration_ms=data.get("duration_ms", int((time.monotonic() - started) * 1000)),
            languages_requested=job.languages,
            warnings=data.get("warnings", []),
        )


@register
class EasyOCREngine(RemoteSidecarEngine):
    name = "easyocr"
    display_name_en = "EasyOCR"
    display_name_ar = "EasyOCR"
    description_en = "Neural OCR with word-level boxes. A useful second opinion on printed text."
    description_ar = "تعرف ضوئي عصبي مع مربعات للكلمات. رأي ثانٍ مفيد للنصوص المطبوعة."
    tier = "neural"
    endpoint_setting = "EASYOCR_URL"
    supported_languages = {"ara", "eng"}
    supports_boxes = True
    supports_confidence = True
    preferred_profile = "neural"
    preferred_dpi = 200
    est_seconds_per_page = 8.0
    default_timeout_s = 300
    queue = "ocr_cpu"
