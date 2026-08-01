"""Surya OCR 2, served by the host Ollama.

Surya is a TWO-STAGE model and the protocol below was established empirically
against `melashri/surya-ocr-2:q4_k_m`. Do not "simplify" it into a single call —
a full page always returns layout, never text, whatever prompt you send.

  Stage 1 (layout): send the full page. Returns JSON:
      [{"label": "Section-Header", "bbox": "316 97 654 162", "count": 30}, ...]
      bbox is "x0 y0 x1 y1", normalized to 0..1000 on BOTH axes.
      Observed labels: Section-Header, Text, Table, Picture, List-item.

  Stage 2 (recognition): crop each block and send the crop with the prompt
      "ocr_with_boxes". Returns an HTML fragment (<p>, <b>, <table>...) holding
      that block's text.

Consequences, all good:
  * supports_boxes is True (block-level, not line-level)
  * tables come back as real HTML structure, kept in raw_payload for later
    table extraction
  * cost is N+1 Ollama calls per page, so it is slower than a single-shot VLM
"""
from __future__ import annotations

import json
import logging
import re
import time

from django.conf import settings

from common.arabic import arabic_char_ratio
from common.ollama import OllamaError, OllamaUnavailable, get_client

from .base import (
    BBox,
    EngineHealth,
    OCRJobInput,
    OCRLine,
    OCREngine,
    OCRPage,
    OCRResult,
    PageCallback,
    TransientEngineError,
)
from .registry import register

logger = logging.getLogger(__name__)

SURYA_MODEL = "melashri/surya-ocr-2:q4_k_m"

LAYOUT_PROMPT = "layout"
RECOGNITION_PROMPT = "ocr_with_boxes"

# Blocks that carry no text worth transcribing.
SKIP_LABELS = {"Picture", "Figure", "Separator", "PageHeader", "PageFooter"}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def html_to_text(html: str) -> str:
    """Flatten Surya's HTML fragment to plain text, preserving row/cell breaks."""
    if not html:
        return ""
    text = html
    # Table cells become tab-separated, rows become newlines, so the plain-text
    # revision keeps a readable table shape.
    text = re.sub(r"</t[dh]>\s*", "\t", text)
    text = re.sub(r"</tr>\s*", "\n", text)
    text = re.sub(r"</(p|div|h[1-6]|li)>\s*", "\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    lines = [_WS_RE.sub(" ", ln).strip(" \t") for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln.strip())


def parse_layout(raw: str) -> list[dict]:
    """Parse stage-1 JSON. Tolerant: the model occasionally wraps it in prose."""
    if not raw:
        return []
    candidate = raw.strip()
    if not candidate.startswith("["):
        match = re.search(r"\[.*\]", candidate, re.S)
        if not match:
            return []
        candidate = match.group(0)
    try:
        blocks = json.loads(candidate)
    except json.JSONDecodeError:
        logger.warning("surya layout was not valid JSON: %.200s", raw)
        return []

    out = []
    for block in blocks if isinstance(blocks, list) else []:
        bbox = block.get("bbox")
        coords = None
        if isinstance(bbox, str):
            parts = bbox.split()
            if len(parts) == 4:
                try:
                    coords = [float(p) / 1000.0 for p in parts]
                except ValueError:
                    coords = None
        elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            coords = [float(v) / 1000.0 for v in bbox]

        if not coords:
            continue
        x0, y0, x1, y1 = coords
        if x1 <= x0 or y1 <= y0:
            continue
        out.append(
            {
                "label": block.get("label", "Text"),
                "bbox": [max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)],
                "count": block.get("count", 0),
            }
        )
    return out


def reading_order(blocks: list[dict], rtl: bool) -> list[dict]:
    """Sort blocks top-to-bottom, then by column.

    For Arabic pages the column order is right-to-left, so a two-column layout
    must read the right column first. Blocks within ~2% vertical distance are
    treated as the same band.
    """
    def key(block):
        x0, y0, x1, y1 = block["bbox"]
        band = round(y0 / 0.02)
        return (band, -x1 if rtl else x0)

    return sorted(blocks, key=key)


@register
class SuryaEngine(OCREngine):
    name = "surya"
    display_name_en = "Surya OCR 2"
    display_name_ar = "سوريا 2"
    description_en = (
        "Layout-aware neural OCR covering 90+ languages. Detects blocks and tables, "
        "then reads each one. Strong on structured documents."
    )
    description_ar = (
        "تعرف ضوئي عصبي يدرك تخطيط الصفحة ويدعم أكثر من ٩٠ لغة. يكتشف الكتل والجداول "
        "ثم يقرأ كلاً منها. قوي مع المستندات المنظمة."
    )
    tier = "neural"
    supported_languages = {"ara", "eng"}
    supports_boxes = True  # block-level, via stage 1
    supports_confidence = False  # the GGUF exposes no logprobs
    preferred_profile = "neural"
    preferred_dpi = 200
    est_seconds_per_page = 12.0
    default_timeout_s = 600
    # Shares the single host Ollama, so it must be serialized on the llm queue.
    queue = "llm"

    def health(self) -> EngineHealth:
        client = get_client()
        try:
            models = client.tags()
        except OllamaUnavailable as exc:
            return EngineHealth(available=False, detail=str(exc))

        names = {m.get("name", "") for m in models}
        if SURYA_MODEL not in names:
            base = SURYA_MODEL.split(":")[0]
            if not any(n.split(":")[0] == base for n in names):
                return EngineHealth(
                    available=False,
                    model_id=SURYA_MODEL,
                    detail=f"model not pulled — run: ollama pull {SURYA_MODEL}",
                )

        if not client.supports_vision(SURYA_MODEL):
            return EngineHealth(
                available=False,
                model_id=SURYA_MODEL,
                detail="model does not report vision capability",
            )

        return EngineHealth(
            available=True,
            model_id=SURYA_MODEL,
            version="surya-ocr-2",
            detail="Two-stage: layout detection, then per-block recognition.",
        )

    def run(self, job: OCRJobInput, on_page: PageCallback | None = None) -> OCRResult:
        from PIL import Image

        client = get_client()
        started = time.monotonic()
        pages: list[OCRPage] = []
        warnings: list[str] = []

        for page_image in job.page_images:
            page_started = time.monotonic()
            image = Image.open(page_image.path).convert("RGB")
            width, height = image.size

            # ---- stage 1: layout -------------------------------------------
            try:
                layout_raw = client.generate(
                    SURYA_MODEL,
                    LAYOUT_PROMPT,
                    images=[page_image.path],
                    timeout=job.deadline_s,
                )
            except OllamaUnavailable as exc:
                raise TransientEngineError(str(exc)) from exc
            except OllamaError as exc:
                raise TransientEngineError(f"surya layout failed: {exc}") from exc

            blocks = parse_layout(layout_raw)
            if not blocks:
                # Fall back to treating the whole page as one block rather than
                # returning nothing.
                blocks = [{"label": "Text", "bbox": [0.0, 0.0, 1.0, 1.0], "count": 0}]
                warnings.append(f"page {page_image.page_number}: layout empty, read whole page")

            rtl = "ara" in job.languages or "ar" in job.languages
            blocks = reading_order(blocks, rtl=rtl)

            # ---- stage 2: recognition per block ----------------------------
            lines: list[OCRLine] = []
            block_html: list[dict] = []
            crop_dir = _crop_dir(page_image.path)

            for index, block in enumerate(blocks):
                if block["label"] in SKIP_LABELS:
                    continue

                x0, y0, x1, y1 = block["bbox"]
                box = (
                    max(0, int(x0 * width)),
                    max(0, int(y0 * height)),
                    min(width, int(x1 * width)),
                    min(height, int(y1 * height)),
                )
                if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                    continue

                crop_path = f"{crop_dir}/b{page_image.page_number}_{index}.png"
                image.crop(box).save(crop_path)

                try:
                    html = client.generate(
                        SURYA_MODEL,
                        RECOGNITION_PROMPT,
                        images=[crop_path],
                        timeout=job.deadline_s,
                    )
                except OllamaError as exc:
                    warnings.append(f"block {index} on page {page_image.page_number}: {exc}")
                    continue
                finally:
                    _unlink(crop_path)

                text = html_to_text(html)
                if not text.strip():
                    continue

                block_html.append({"label": block["label"], "bbox": block["bbox"], "html": html})
                lines.append(
                    OCRLine(
                        text=text,
                        bbox=BBox(x0, y0, x1, y1),
                        confidence=None,
                        direction="rtl" if arabic_char_ratio(text) > 0.4 else "ltr",
                        label=block["label"],
                    )
                )

            page = OCRPage(
                page_number=page_image.page_number,
                text="\n\n".join(line.text for line in lines),
                lines=lines,
                confidence=None,
                width_px=width,
                height_px=height,
                duration_ms=int((time.monotonic() - page_started) * 1000),
                # The HTML is kept so table structure can be recovered later
                # without re-running the model.
                raw={"blocks": block_html},
            )
            pages.append(page)
            if on_page:
                on_page(page)

        return OCRResult(
            engine=self.name,
            engine_version="surya-ocr-2",
            model_id=SURYA_MODEL,
            pages=pages,
            duration_ms=int((time.monotonic() - started) * 1000),
            languages_requested=job.languages,
            warnings=warnings,
        )


def _crop_dir(page_path: str) -> str:
    import os

    path = os.path.join(os.path.dirname(page_path), "_crops")
    os.makedirs(path, exist_ok=True)
    return path


def _unlink(path: str) -> None:
    import os

    try:
        os.unlink(path)
    except OSError:
        pass
