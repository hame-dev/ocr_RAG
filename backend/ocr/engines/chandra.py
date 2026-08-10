"""Chandra OCR 2 (datalab-to/chandra-ocr-2), in two flavours.

There are two ways to run this model, and they behave differently enough that
each gets its own engine and its own row in the comparison grid:

  `chandra`        the official CLI (`pip install chandra-ocr[hf]`). Full
                   fidelity, but a heavy local install and no boxes back.
  `chandra_ollama` a community GGUF port served by the host Ollama
                   (fredrezones55/chandra-ocr-2). No Python install at all, and
                   it returns layout boxes — see ChandraOllamaEngine below.

Most users want the Ollama one; the CLI stays for anyone who needs the exact
upstream pipeline.

--- The CLI engine -----------------------------------------------------------

The `chandra` console script is a transformers model, so this adapter shells
out rather than speaking HTTP, and that shapes the whole implementation:

  * ONE subprocess per job, not per page. Loading ~9GB of weights takes far
    longer than transcribing a page, so per-page invocation would pay that cost
    N times. The CLI already accepts a whole PDF.
  * `--paginate_output` is mandatory for us. Without it the CLI concatenates
    every page into one markdown blob with no separator, and we could not
    rebuild the per-page structure the comparison grid and page-mapper need.
  * The CLI catches per-file exceptions, prints them, and still exits 0. So a
    zero exit code proves nothing; the real success signal is whether the
    output markdown file exists.

Output layout, from chandra/scripts/cli.py::save_merged_output:
    <output_dir>/<stem>/<stem>.md              merged markdown  (what we read)
    <output_dir>/<stem>/<stem>.html            merged HTML
    <output_dir>/<stem>/<stem>_metadata.json   page count, token counts, boxes

Chandra emits markdown by design (tables, headings, math). We keep that verbatim
as the page text — the chat renders markdown, and flattening it here would throw
away the table structure that is the model's main advantage.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from django.conf import settings

from common.arabic import arabic_char_ratio
from common.ollama import OllamaError, OllamaUnavailable, get_client

from .base import (
    BBox,
    EngineHealth,
    EngineNotApplicable,
    OCREngine,
    OCRJobInput,
    OCRLine,
    OCRPage,
    OCRResult,
    PageCallback,
    TransientEngineError,
)
from .registry import register
# Both helpers are exactly right here: Chandra's Ollama build returns the same
# HTML-ish fragments and the same 0..1000 box convention as Surya.
from .surya import html_to_text, reading_order

logger = logging.getLogger(__name__)

MODEL_ID = "datalab-to/chandra-ocr-2"

# The CLI writes this between pages when --paginate_output is set:
#     "\n\n{page_num}" + "-" * 48 + "\n\n"
# page_num is the 0-based index of the page that just ended, so the marker
# before page 2 is "1----...". Matched on its own line, anchored, so a run of
# dashes inside a real markdown table can never be mistaken for a separator.
PAGE_BREAK_RE = re.compile(r"^\d+-{48,}$", re.MULTILINE)


def split_pages(markdown: str) -> list[str]:
    """Split the CLI's merged markdown back into per-page texts."""
    if not markdown:
        return []
    return [part.strip("\n") for part in PAGE_BREAK_RE.split(markdown)]


@register
class ChandraEngine(OCREngine):
    name = "chandra"
    display_name_en = "Chandra OCR 2"
    display_name_ar = "شاندرا 2"
    description_en = (
        "State-of-the-art layout OCR outputting markdown. Excellent on tables, math, "
        "forms and handwriting; 90+ languages. Slow locally — needs its own weights."
    )
    description_ar = (
        "تعرف ضوئي متقدم يدرك التخطيط وينتج ماركداون. ممتاز مع الجداول والمعادلات "
        "والنماذج والكتابة اليدوية، ويدعم أكثر من ٩٠ لغة. بطيء محلياً ويتطلب أوزانه الخاصة."
    )
    tier = "vlm"
    supported_languages = {"ara", "eng"}
    # The metadata JSON carries page boxes, not line boxes, and the markdown we
    # keep as text has no coordinates attached — so no overlay.
    supports_boxes = False
    supports_confidence = False
    preferred_profile = "vlm"
    preferred_dpi = 200
    est_seconds_per_page = 45.0
    default_timeout_s = 1800
    # A local transformers model that wants the whole GPU/unified memory. It must
    # not run beside the Ollama VLMs, so it shares the serialized llm queue.
    queue = "llm"

    # ---- health -------------------------------------------------------------

    def health(self) -> EngineHealth:
        binary = shutil.which(settings.CHANDRA_BIN)
        if not binary:
            return EngineHealth(
                available=False,
                model_id=MODEL_ID,
                detail=(
                    f"{settings.CHANDRA_BIN!r} not on PATH — "
                    "run: pip install 'chandra-ocr[hf]'"
                ),
            )

        method = (settings.CHANDRA_METHOD or "hf").lower()
        if method not in {"hf", "vllm"}:
            return EngineHealth(
                available=False,
                model_id=MODEL_ID,
                detail=f"CHANDRA_METHOD must be 'hf' or 'vllm', got {method!r}",
            )

        # `--method hf` needs torch; the CLI would otherwise fail deep inside a
        # page loop after we have already spent minutes rasterizing.
        if method == "hf":
            try:
                import torch  # noqa: F401
            except Exception as exc:
                return EngineHealth(
                    available=False,
                    model_id=MODEL_ID,
                    detail=f"torch missing for --method hf ({exc}); pip install 'chandra-ocr[hf]'",
                )

        version = ""
        try:
            from importlib.metadata import version as pkg_version

            version = pkg_version("chandra-ocr")
        except Exception:
            logger.debug("could not read chandra-ocr version", exc_info=True)

        return EngineHealth(
            available=True,
            model_id=MODEL_ID,
            version=version or method,
            detail=f"method={method}",
        )

    # ---- run ----------------------------------------------------------------

    def run(self, job: OCRJobInput, on_page: PageCallback | None = None) -> OCRResult:
        method = (settings.CHANDRA_METHOD or "hf").lower()
        started = time.monotonic()

        # Prefer the original PDF: Chandra does its own rasterization and page
        # splitting, and handing it the source keeps its layout model on the
        # highest-fidelity input available. Fall back to page rasters otherwise.
        source = job.source_path
        use_source_pdf = bool(source) and Path(source).suffix.lower() == ".pdf" and Path(source).exists()

        if not use_source_pdf and not job.page_images:
            raise EngineNotApplicable("chandra needs either a source PDF or page rasters")

        with tempfile.TemporaryDirectory(prefix="chandra-") as tmp:
            tmp_path = Path(tmp)
            input_path, expected_stems = self._stage_input(job, tmp_path, use_source_pdf)
            output_dir = tmp_path / "out"

            markdown_by_stem = self._invoke(input_path, output_dir, method, job)

            texts: list[str] = []
            for stem in expected_stems:
                texts.extend(split_pages(markdown_by_stem.get(stem, "")))

        if not any(t.strip() for t in texts):
            raise TransientEngineError(
                "chandra produced no text — check the worker log for the CLI's own error line"
            )

        pages = self._build_pages(job, texts, on_page)

        return OCRResult(
            engine=self.name,
            engine_version=method,
            model_id=MODEL_ID,
            pages=pages,
            duration_ms=int((time.monotonic() - started) * 1000),
            languages_requested=job.languages,
            warnings=self._page_count_warnings(job, texts),
        )

    def _stage_input(
        self, job: OCRJobInput, tmp_path: Path, use_source_pdf: bool
    ) -> tuple[Path, list[str]]:
        """Give the CLI one input path, and record the output stems to read back.

        Symlinks rather than copies: a scanned PDF can be hundreds of MB and the
        CLI only ever reads it.
        """
        if use_source_pdf:
            src = Path(job.source_path)
            # The CLI derives the output folder from the input stem, so a stable
            # ASCII stem avoids any surprise with a non-ASCII original filename.
            staged = tmp_path / f"doc{src.suffix.lower()}"
            _link_or_copy(src, staged)
            return staged, [staged.stem]

        # Raster fallback: one directory of page images, processed in filename
        # order. Zero-padded so page 10 sorts after page 9, matching the CLI's
        # sorted() over the glob.
        pages_dir = tmp_path / "pages"
        pages_dir.mkdir()
        stems: list[str] = []
        for image in sorted(job.page_images, key=lambda p: p.page_number):
            suffix = Path(image.path).suffix.lower() or ".png"
            staged = pages_dir / f"p{image.page_number:05d}{suffix}"
            _link_or_copy(Path(image.path), staged)
            stems.append(staged.stem)
        return pages_dir, stems

    def _invoke(
        self, input_path: Path, output_dir: Path, method: str, job: OCRJobInput
    ) -> dict[str, str]:
        cmd = [
            settings.CHANDRA_BIN,
            str(input_path),
            str(output_dir),
            "--method",
            method,
            # Required: without it the pages come back as one undivided blob.
            "--paginate_output",
            # We keep only text, so skip writing images and the HTML twin.
            "--no-images",
            "--no-html",
        ]
        if settings.CHANDRA_BATCH_SIZE:
            cmd += ["--batch-size", str(settings.CHANDRA_BATCH_SIZE)]

        timeout = max(job.deadline_s, settings.CHANDRA_TIMEOUT_S)
        logger.info("chandra: %s (timeout %ss)", " ".join(cmd), timeout)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                # Never inherit a stray CUDA_VISIBLE_DEVICES from the worker.
                env={**os.environ, "TOKENIZERS_PARALLELISM": "false"},
            )
        except subprocess.TimeoutExpired as exc:
            raise TransientEngineError(
                f"chandra timed out after {timeout}s (first run also downloads ~9GB of weights)"
            ) from exc
        except OSError as exc:
            raise TransientEngineError(f"could not launch {settings.CHANDRA_BIN!r}: {exc}") from exc

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-800:]
            raise TransientEngineError(f"chandra exited {proc.returncode}: {tail}")

        # The CLI catches per-file errors and still exits 0, so a clean exit code
        # is not evidence of success — missing output is how failure shows up.
        markdown = _collect_markdown(output_dir)
        if not markdown:
            tail = (proc.stderr or proc.stdout or "").strip()[-800:]
            raise TransientEngineError(f"chandra wrote no markdown output: {tail}")
        return markdown

    def _build_pages(
        self, job: OCRJobInput, texts: list[str], on_page: PageCallback | None
    ) -> list[OCRPage]:
        # Page geometry comes from the rasters we already made, so the viewer
        # keeps working even though Chandra reports no boxes.
        sizes = {p.page_number: (p.width_px, p.height_px) for p in job.page_images}
        pages: list[OCRPage] = []

        for index, text in enumerate(texts, start=1):
            width, height = sizes.get(index, (0, 0))
            page = OCRPage(
                page_number=index,
                text=text,
                lines=[
                    OCRLine(
                        text=line,
                        direction="rtl" if arabic_char_ratio(line) > 0.4 else "ltr",
                    )
                    for line in text.split("\n")
                    if line.strip()
                ],
                confidence=None,
                width_px=width,
                height_px=height,
                raw={"format": "markdown"},
            )
            pages.append(page)
            if on_page:
                on_page(page)
        return pages

    def _page_count_warnings(self, job: OCRJobInput, texts: list[str]) -> list[str]:
        expected = len(job.page_images)
        if expected and len(texts) != expected:
            # Surfaced rather than raised: a partial transcript is still worth
            # comparing, but the operator must see that pages went missing.
            return [
                f"chandra returned {len(texts)} page(s) for a {expected}-page document"
            ]
        return []


def _link_or_copy(src: Path, dest: Path) -> None:
    """Symlink into the temp dir, copying only if the filesystem refuses."""
    try:
        dest.symlink_to(src)
    except OSError:
        shutil.copy2(src, dest)


def _collect_markdown(output_dir: Path) -> dict[str, str]:
    """Read every `<stem>/<stem>.md` the CLI produced, keyed by stem."""
    if not output_dir.is_dir():
        return {}
    out: dict[str, str] = {}
    for child in sorted(output_dir.iterdir()):
        if not child.is_dir():
            continue
        markdown_file = child / f"{child.name}.md"
        if markdown_file.exists():
            try:
                out[child.name] = markdown_file.read_text(encoding="utf-8")
            except OSError:
                logger.warning("could not read %s", markdown_file, exc_info=True)
    return out


def read_metadata(output_dir: Path, stem: str) -> dict:
    """The CLI's sidecar metadata (page count, token counts). Best-effort."""
    path = output_dir / stem / f"{stem}_metadata.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# The Ollama flavour
# ---------------------------------------------------------------------------

OLLAMA_MODEL = "fredrezones55/chandra-ocr-2:latest"

# Established empirically against that build (there is a second, unpatched
# upload on Ollama that reports no vision capability — hence the explicit
# vision check in health()).
#
# Its Modelfile template is a bare "{{ .Prompt }}" with no chat wrapper, so the
# prompt text is essentially ignored: "ocr", "ocr_layout" and a full English
# instruction all returned byte-identical output. It always answers in one
# format, a flat run of divs:
#
#   <div data-bbox="43 217 190 276" data-label="Text"><p>INVOICE ...</p></div>
#
# data-bbox is "x0 y0 x1 y1" normalized to 0..1000 on BOTH axes — verified by
# probing 400x1000 and 1600x500 rasters and checking the numbers track the
# fraction of each dimension, not the pixel count. That is the same convention
# Surya uses, so BBox construction matches.
CHANDRA_OLLAMA_PROMPT = "ocr_layout"

_DIV_RE = re.compile(
    r'<div\b[^>]*?data-bbox="(?P<bbox>[^"]*)"[^>]*?>(?P<body>.*?)</div>',
    re.S | re.I,
)
_LABEL_RE = re.compile(r'data-label="([^"]*)"', re.I)

# Blocks carrying no transcribable text. Mirrors surya's list so the two
# layout-aware engines agree on what to drop.
SKIP_LABELS = {"Picture", "Figure", "Separator", "PageHeader", "PageFooter"}


def parse_layout_html(raw: str) -> list[dict]:
    """Parse the div run into {text, bbox, label} blocks, in document order.

    Tolerant by design: a truncated response loses its final </div>, and a
    block with an unparseable bbox is kept with bbox=None rather than dropped —
    losing a coordinate must never lose the text.
    """
    if not raw:
        return []

    blocks: list[dict] = []
    for match in _DIV_RE.finditer(raw):
        body = match.group("body")
        text = html_to_text(body)
        if not text.strip():
            continue

        label = ""
        attrs = match.group(0)[: match.start("body") - match.start(0)]
        label_match = _LABEL_RE.search(attrs)
        if label_match:
            label = label_match.group(1)
        if label in SKIP_LABELS:
            continue

        blocks.append({"text": text, "bbox": _parse_bbox(match.group("bbox")), "label": label})

    if blocks:
        return blocks

    # No divs at all — the model occasionally answers in plain text. Keep it
    # rather than reporting an empty page.
    fallback = html_to_text(raw).strip()
    return [{"text": fallback, "bbox": None, "label": ""}] if fallback else []


def _parse_bbox(raw: str) -> BBox | None:
    parts = (raw or "").replace(",", " ").split()
    if len(parts) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(p) / 1000.0 for p in parts)
    except ValueError:
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return BBox(
        x0=max(0.0, min(1.0, x0)),
        y0=max(0.0, min(1.0, y0)),
        x1=max(0.0, min(1.0, x1)),
        y1=max(0.0, min(1.0, y1)),
    )


@register
class ChandraOllamaEngine(OCREngine):
    """Chandra OCR 2 via the host Ollama — no Python install, and it returns boxes."""

    name = "chandra_ollama"
    display_name_en = "Chandra OCR 2 (Ollama)"
    display_name_ar = "شاندرا 2 (أولاما)"
    description_en = (
        "Layout-aware OCR returning text plus block boxes. Strong on tables, forms "
        "and handwriting across 90+ languages. Runs on the local Ollama."
    )
    description_ar = (
        "تعرف ضوئي يدرك التخطيط ويعيد النص مع مواضع الكتل. قوي مع الجداول والنماذج "
        "والكتابة اليدوية بأكثر من ٩٠ لغة. يعمل عبر أولاما محلياً."
    )
    tier = "vlm"
    supported_languages = {"ara", "eng"}
    # Block-level, from data-bbox — the CLI flavour cannot do this.
    supports_boxes = True
    supports_confidence = False  # the GGUF exposes no logprobs
    preferred_profile = "vlm"
    preferred_dpi = 200
    # Measured ~3.5s on a sparse page; a dense one is several times that, so
    # this stays conservative rather than promising the best case.
    est_seconds_per_page = 12.0
    default_timeout_s = 900
    queue = "llm"

    def health(self) -> EngineHealth:
        client = get_client()
        try:
            models = client.tags()
        except OllamaUnavailable as exc:
            return EngineHealth(available=False, model_id=OLLAMA_MODEL, detail=str(exc))

        names = {m.get("name", "") for m in models}
        base = OLLAMA_MODEL.split(":")[0]
        resolved = OLLAMA_MODEL if OLLAMA_MODEL in names else None
        if not resolved:
            resolved = next((n for n in names if n.split(":")[0] == base), None)

        if not resolved:
            return EngineHealth(
                available=False,
                model_id=OLLAMA_MODEL,
                detail=f"model not pulled — run: ollama pull {OLLAMA_MODEL}",
            )

        # There is a second upload of this model on Ollama without the vision
        # patch. It pulls fine and then returns nothing useful, so refuse it
        # here rather than offering an engine that silently produces blanks.
        if not client.supports_vision(resolved):
            return EngineHealth(
                available=False,
                model_id=resolved,
                detail=(
                    "this build reports no vision capability — use the patched "
                    f"{OLLAMA_MODEL}"
                ),
            )

        return EngineHealth(available=True, model_id=resolved, version=resolved)

    def run(self, job: OCRJobInput, on_page: PageCallback | None = None) -> OCRResult:
        client = get_client()
        model = self.health().model_id or OLLAMA_MODEL

        started = time.monotonic()
        pages: list[OCRPage] = []

        for page_image in job.page_images:
            page_started = time.monotonic()
            try:
                raw = client.generate(
                    model,
                    CHANDRA_OLLAMA_PROMPT,
                    images=[page_image.path],
                    # Bare "{{ .Prompt }}" template: a system message would be
                    # prepended as literal text for the model to transcribe.
                    system=None,
                    timeout=job.deadline_s,
                    think=False,
                    # The Modelfile ships temperature 1 with presence_penalty
                    # 1.5 — creative-writing defaults. Transcription must be
                    # deterministic, and the penalty actively suppresses the
                    # repeated tokens that real documents are full of.
                    options={"temperature": 0, "presence_penalty": 0, "top_p": 1},
                )
            except OllamaUnavailable as exc:
                raise TransientEngineError(str(exc)) from exc
            except OllamaError as exc:
                raise TransientEngineError(f"chandra_ollama failed: {exc}") from exc

            blocks = parse_layout_html(raw)
            rtl = arabic_char_ratio(" ".join(b["text"] for b in blocks)) > 0.4
            # reading_order sorts on a plain [x0,y0,x1,y1] list, so pass the
            # BBox alongside rather than round-tripping it through one.
            ordered = reading_order(
                [
                    {
                        **b,
                        "bbox": b["bbox"].as_list() if b["bbox"] else [0.0, 0.0, 1.0, 1.0],
                        "_bbox": b["bbox"],
                    }
                    for b in blocks
                ],
                rtl,
            )

            lines: list[OCRLine] = []
            for block in ordered:
                bbox = block["_bbox"]
                for line in block["text"].split("\n"):
                    if not line.strip():
                        continue
                    lines.append(
                        OCRLine(
                            text=line,
                            bbox=bbox,
                            direction="rtl" if arabic_char_ratio(line) > 0.4 else "ltr",
                            label=block.get("label", ""),
                        )
                    )

            page = OCRPage(
                page_number=page_image.page_number,
                text="\n".join(line.text for line in lines),
                lines=lines,
                confidence=None,
                width_px=page_image.width_px,
                height_px=page_image.height_px,
                duration_ms=int((time.monotonic() - page_started) * 1000),
                raw={"html": raw},
            )
            pages.append(page)
            if on_page:
                on_page(page)

        return OCRResult(
            engine=self.name,
            engine_version=model,
            model_id=model,
            pages=pages,
            duration_ms=int((time.monotonic() - started) * 1000),
            languages_requested=job.languages,
        )
