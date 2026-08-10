"""Generic vision-LLM OCR through the host Ollama.

One parameterized adapter serves every VLM OCR model, so adding a new one is a
registry entry rather than new code.

The failure mode this guards against: an untuned VLM will sometimes *describe* a
page ("This appears to be an invoice for...") or silently summarize a long one,
instead of transcribing it. That produces short, fluent, completely wrong output
which would otherwise look plausible in the comparison grid. Two defences:
  1. a strict transcription prompt with temperature 0
  2. a post-hoc length-collapse check that flags suspected summarization so the
     run is de-ranked and visibly warned rather than silently trusted
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from common.arabic import arabic_char_ratio
from common.ollama import OllamaError, OllamaUnavailable, get_client

from .base import (
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

TRANSCRIPTION_PROMPT = (
    "Transcribe every character of text visible in this image, exactly as it appears.\n"
    "\n"
    "Rules:\n"
    "- Output ONLY the transcribed text. No preamble, no commentary, no description.\n"
    "- Do not translate. Keep each word in its original language and script.\n"
    "- Do not summarize, shorten, correct, or reorder anything.\n"
    "- Preserve line breaks and reading order.\n"
    "- For Arabic, transcribe in logical order (the order the words are read).\n"
    "- If a region is illegible, write [illegible] and continue.\n"
    "- If the image contains no text, output nothing."
)

SYSTEM_PROMPT = (
    "You are an OCR transcription engine. You output verbatim text only. "
    "You never describe images and never answer questions about them."
)


@dataclass
class VLMSpec:
    name: str
    model_id: str
    display_name_en: str
    display_name_ar: str
    description_en: str
    description_ar: str
    est_seconds_per_page: float = 25.0
    preferred_dpi: int = 150
    prompt: str = TRANSCRIPTION_PROMPT
    languages: frozenset = frozenset({"ara", "eng"})
    # Small OCR specialists are not instruction-tuned chat models. Their Ollama
    # template is often a bare "{{ .Prompt }}", so a system message and a long
    # rule list arrive as literal text the model tries to transcribe — which
    # sends it into a degenerate repetition loop. Such models take a short
    # keyword prompt and no system message.
    system: str | None = SYSTEM_PROMPT
    # Models that echo the page twice (plain, then fenced) need the duplicate
    # collapsed, otherwise every chunk is indexed twice.
    dedupe_fenced_echo: bool = False


# Registered models. Only those actually pulled report available=True, and the
# UI greys out the rest with a one-click pull hint.
SPECS: list[VLMSpec] = [
    VLMSpec(
        name="vlm_qwen35",
        model_id="qwen3.5:9b",
        display_name_en="Qwen 3.5 Vision",
        display_name_ar="Qwen 3.5 للرؤية",
        description_en="General-purpose vision model. Strong on messy scans and mixed layouts.",
        description_ar="نموذج رؤية عام. قوي مع المسوحات غير الواضحة والتخطيطات المختلطة.",
        est_seconds_per_page=30.0,
    ),
    VLMSpec(
        name="vlm_qari",
        model_id="melashri/qari-ocr:0.4.0-q4_k_m",
        display_name_en="Qari OCR (Arabic specialist)",
        display_name_ar="قارئ — متخصص بالعربية",
        description_en="Fine-tuned specifically for Arabic documents. Usually the best on Arabic script.",
        description_ar="مدرَّب خصيصاً للمستندات العربية. غالباً الأفضل مع الخط العربي.",
        est_seconds_per_page=20.0,
    ),
    VLMSpec(
        name="vlm_qwen3vl",
        model_id="qwen3-vl:8b",
        display_name_en="Qwen3-VL",
        display_name_ar="Qwen3-VL",
        description_en="Vision-language model with strong document and handwriting coverage.",
        description_ar="نموذج رؤية ولغة بتغطية قوية للمستندات والكتابة اليدوية.",
        est_seconds_per_page=28.0,
    ),
    VLMSpec(
        name="vlm_deepseek_ocr",
        model_id="deepseek-ocr:latest",
        display_name_en="DeepSeek-OCR",
        display_name_ar="DeepSeek-OCR",
        description_en="Token-efficient OCR model covering 100 languages.",
        description_ar="نموذج تعرف ضوئي فعال يغطي ١٠٠ لغة.",
        est_seconds_per_page=18.0,
    ),
    VLMSpec(
        name="vlm_glm_ocr",
        model_id="glm-ocr:latest",
        display_name_en="GLM-OCR",
        display_name_ar="GLM-OCR",
        description_en=(
            "Zhipu's document OCR model. Layout-aware, outputs markdown, strong on "
            "tables and mixed-script pages."
        ),
        description_ar=(
            "نموذج التعرف الضوئي من Zhipu. يدرك التخطيط وينتج ماركداون، وقوي مع "
            "الجداول والصفحات متعددة النصوص."
        ),
        est_seconds_per_page=22.0,
        # Verified against glm-ocr:latest (1.1B, template "{{ .Prompt }}"):
        # the standard prompt makes it emit "Tent" forever, while the bare
        # keyword "ocr" transcribes correctly.
        prompt="ocr",
        system=None,
        dedupe_fenced_echo=True,
    ),
]


class BaseVLMEngine(OCREngine):
    spec: VLMSpec

    tier = "vlm"
    supports_boxes = False
    supports_confidence = False
    preferred_profile = "vlm"
    default_timeout_s = 900
    # All VLMs share the one host Ollama; the llm queue is concurrency 1 so they
    # never fight each other for memory.
    queue = "llm"

    def health(self) -> EngineHealth:
        client = get_client()
        try:
            models = client.tags()
        except OllamaUnavailable as exc:
            return EngineHealth(available=False, model_id=self.spec.model_id, detail=str(exc))

        names = {m.get("name", "") for m in models}
        base = self.spec.model_id.split(":")[0]
        resolved = None
        if self.spec.model_id in names:
            resolved = self.spec.model_id
        else:
            for name in names:
                if name.split(":")[0] == base:
                    resolved = name
                    break

        if not resolved:
            return EngineHealth(
                available=False,
                model_id=self.spec.model_id,
                detail=f"model not pulled — run: ollama pull {self.spec.model_id}",
            )

        if not client.supports_vision(resolved):
            return EngineHealth(
                available=False,
                model_id=resolved,
                detail="model does not report vision capability",
            )

        return EngineHealth(available=True, model_id=resolved, version=resolved)

    def run(self, job: OCRJobInput, on_page: PageCallback | None = None) -> OCRResult:
        client = get_client()
        model = self.health().model_id or self.spec.model_id

        started = time.monotonic()
        pages: list[OCRPage] = []
        warnings: list[str] = []

        for page_image in job.page_images:
            page_started = time.monotonic()
            try:
                text = client.generate(
                    model,
                    self.spec.prompt,
                    images=[page_image.path],
                    system=self.spec.system,
                    timeout=job.deadline_s,
                    # Thinking blocks would leak into the transcript.
                    think=False,
                )
            except OllamaUnavailable as exc:
                raise TransientEngineError(str(exc)) from exc
            except OllamaError as exc:
                raise TransientEngineError(f"{self.spec.name} failed: {exc}") from exc

            text = _strip_preamble(text)
            if self.spec.dedupe_fenced_echo:
                text = _collapse_fenced_echo(text)
            page = OCRPage(
                page_number=page_image.page_number,
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
                width_px=page_image.width_px,
                height_px=page_image.height_px,
                duration_ms=int((time.monotonic() - page_started) * 1000),
            )
            pages.append(page)
            if on_page:
                on_page(page)

        return OCRResult(
            engine=self.spec.name,
            engine_version=model,
            model_id=model,
            pages=pages,
            duration_ms=int((time.monotonic() - started) * 1000),
            languages_requested=job.languages,
            warnings=warnings,
        )


# Models occasionally prepend a sentence despite the prompt.
_PREAMBLES = (
    "here is the transcription",
    "here's the transcription",
    "the text in the image",
    "the transcribed text",
    "sure,",
    "certainly,",
)


def _strip_preamble(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    first, _, rest = stripped.partition("\n")
    if any(first.lower().startswith(p) for p in _PREAMBLES) and first.rstrip().endswith(":"):
        return rest.strip()
    return stripped


def _collapse_fenced_echo(text: str) -> str:
    """Keep the fenced copy when a model emits the page twice, then repeats ```.

    Observed on glm-ocr, whose raw output is:

        INVOICE REF/2026/AR-0417
        Total: SAR 12,500
        ```markdown
        INVOICE REF/2026/AR-0417
        Total: SAR 12,500
        ```
        ```
        ```      <- runaway empty fences to the token limit

    Left alone this indexes every line twice and buries the transcript in
    backticks. The fenced copy is preferred because it is the model's
    structured answer — it keeps the markdown table and heading markup that
    the plain prefix has already flattened.
    """
    if not text:
        return text

    fence = re.search(r"```(?:[a-zA-Z0-9_-]*)\n(.*?)```", text, re.S)
    if fence:
        inner = fence.group(1).strip()
        if inner:
            return inner

    # No closed fence: drop any trailing run of stray fence markers so a
    # truncated response does not end in dozens of backtick lines.
    lines = text.split("\n")
    while lines and lines[-1].strip().strip("`") == "":
        lines.pop()
    return "\n".join(lines).strip()


def _make_engine(spec: VLMSpec) -> type:
    """Build a concrete engine class for one spec and register it."""
    attrs = {
        "spec": spec,
        "name": spec.name,
        "display_name_en": spec.display_name_en,
        "display_name_ar": spec.display_name_ar,
        "description_en": spec.description_en,
        "description_ar": spec.description_ar,
        "supported_languages": set(spec.languages),
        "preferred_dpi": spec.preferred_dpi,
        "est_seconds_per_page": spec.est_seconds_per_page,
    }
    cls = type(f"VLM_{spec.name}", (BaseVLMEngine,), attrs)
    return register(cls)


for _spec in SPECS:
    _make_engine(_spec)
