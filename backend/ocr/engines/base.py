"""The OCR engine contract.

Every engine implements `OCREngine`. The comparison UI, the consensus logic and
the correction guards all depend on this shape, so it is deliberately explicit.

Conventions that hold across all engines:
  * pages are 1-based
  * bounding boxes are normalized to 0..1 relative to the page raster, so the
    frontend can overlay them at any zoom without knowing the source DPI
  * page texts are joined with \\f (form feed) — one page-separator convention
    for the chunker, the editor, the diff and the page mapper
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Literal

Tier = Literal["native", "classical", "neural", "vlm"]

PAGE_SEP = "\f"


class EngineNotApplicable(Exception):
    """Raised when an engine cannot run on this input (not a failure)."""


class TransientEngineError(Exception):
    """A retryable failure — a crashed sidecar, a connection reset."""


@dataclass(frozen=True)
class BBox:
    """Normalized 0..1, origin top-left."""

    x0: float
    y0: float
    x1: float
    y1: float

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            int(self.x0 * width),
            int(self.y0 * height),
            int(self.x1 * width),
            int(self.y1 * height),
        )

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


@dataclass
class OCRWord:
    text: str
    bbox: BBox | None = None
    confidence: float | None = None


@dataclass
class OCRLine:
    text: str
    bbox: BBox | None = None
    confidence: float | None = None
    direction: Literal["rtl", "ltr", "unknown"] = "unknown"
    label: str = ""  # engines with layout analysis report e.g. "Table"
    words: list[OCRWord] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "t": self.text,
            "bbox": self.bbox.as_list() if self.bbox else None,
            "c": self.confidence,
            "dir": self.direction,
            "label": self.label,
            "words": [
                {"t": w.text, "bbox": w.bbox.as_list() if w.bbox else None, "c": w.confidence}
                for w in self.words
            ],
        }


@dataclass
class OCRPage:
    page_number: int
    text: str
    lines: list[OCRLine] = field(default_factory=list)
    confidence: float | None = None
    width_px: int = 0
    height_px: int = 0
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class OCRResult:
    engine: str
    engine_version: str = ""
    model_id: str | None = None
    pages: list[OCRPage] = field(default_factory=list)
    duration_ms: int = 0
    languages_requested: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return PAGE_SEP.join(p.text for p in self.pages)

    @property
    def mean_confidence(self) -> float | None:
        values = [p.confidence for p in self.pages if p.confidence is not None]
        if not values:
            return None
        return sum(values) / len(values)


@dataclass
class PageImage:
    page_number: int
    path: str  # absolute, inside the shared /data/media volume
    dpi: int = 200
    width_px: int = 0
    height_px: int = 0


@dataclass
class OCRJobInput:
    document_id: str
    source_path: str | None
    page_images: list[PageImage]
    languages: list[str] = field(default_factory=lambda: ["ara", "eng"])
    options: dict = field(default_factory=dict)
    deadline_s: int = 300
    # Populated for native_pdf; empty otherwise.
    native_page_texts: list[str] = field(default_factory=list)


@dataclass
class EngineHealth:
    available: bool
    version: str | None = None
    model_id: str | None = None
    detail: str = ""
    latency_ms: int | None = None

    def to_json(self) -> dict:
        return {
            "available": self.available,
            "version": self.version,
            "model_id": self.model_id,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
        }


# Called after each page so slow engines can drive a live progress bar instead
# of leaving the user staring at a spinner for minutes.
PageCallback = Callable[[OCRPage], None]


class OCREngine(ABC):
    name: str = ""
    display_name_en: str = ""
    display_name_ar: str = ""
    tier: Tier = "classical"
    description_en: str = ""
    description_ar: str = ""

    supported_languages: set[str] = {"ara", "eng"}
    supports_boxes: bool = False
    supports_confidence: bool = False
    needs_raster: bool = True
    preferred_profile: str = "neural"
    preferred_dpi: int = 200
    est_seconds_per_page: float = 5.0
    default_timeout_s: int = 300
    # VLM engines share the single host Ollama, so they must be serialized onto
    # the `llm` queue rather than fanned out across CPU workers.
    queue: str = "ocr_cpu"

    @abstractmethod
    def health(self) -> EngineHealth:
        """Real availability check — not an import test."""

    @abstractmethod
    def run(self, job: OCRJobInput, on_page: PageCallback | None = None) -> OCRResult:
        """Transcribe. Must not swallow errors; the task wrapper handles them."""

    def catalog_entry(self, health: EngineHealth | None = None) -> dict:
        health = health or EngineHealth(available=False, detail="not probed")
        return {
            "name": self.name,
            "display_name_en": self.display_name_en or self.name,
            "display_name_ar": self.display_name_ar or self.name,
            "description_en": self.description_en,
            "description_ar": self.description_ar,
            "tier": self.tier,
            "supported_languages": sorted(self.supported_languages),
            "supports_boxes": self.supports_boxes,
            "supports_confidence": self.supports_confidence,
            "preferred_profile": self.preferred_profile,
            "preferred_dpi": self.preferred_dpi,
            "est_seconds_per_page": self.est_seconds_per_page,
            "queue": self.queue,
            **health.to_json(),
        }
