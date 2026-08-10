"""Post-processing of raw VLM output.

Small OCR specialists emit the page twice and then run away into empty code
fences. These cases are transcribed from real glm-ocr:latest responses.
"""
from ocr.engines.vlm_ollama import SPECS, _collapse_fenced_echo


def test_keeps_the_fenced_copy_and_drops_the_plain_echo():
    raw = (
        "INVOICE REF/2026/AR-0417\n\nTotal: SAR 12,500\n"
        "```markdown\n\nINVOICE REF/2026/AR-0417\n\nTotal: SAR 12,500\n```\n"
        "\n```\n```\n```\n```\n"
    )

    assert _collapse_fenced_echo(raw) == "INVOICE REF/2026/AR-0417\n\nTotal: SAR 12,500"


def test_trailing_empty_fences_are_dropped_when_nothing_is_fenced():
    raw = "الطرف الأول: شركة النور\nالطرف الثاني: أحمد الراشد\n```\n```\n```\n"

    assert _collapse_fenced_echo(raw) == (
        "الطرف الأول: شركة النور\nالطرف الثاني: أحمد الراشد"
    )


def test_plain_text_passes_through_untouched():
    raw = "Al-Noor Trading Ltd\nSAR 12,500"
    assert _collapse_fenced_echo(raw) == raw


def test_empty_output_is_safe():
    assert _collapse_fenced_echo("") == ""


def test_glm_spec_uses_the_bare_prompt_that_actually_works():
    """The long instruction prompt sends glm-ocr into a repetition loop.

    Its Ollama template is a bare "{{ .Prompt }}" with no chat wrapper, so a
    system message and a rule list arrive as literal text to transcribe.
    """
    glm = next(s for s in SPECS if s.name == "vlm_glm_ocr")

    assert glm.prompt == "ocr"
    assert glm.system is None
    assert glm.dedupe_fenced_echo is True


def test_chat_tuned_specs_keep_the_strict_transcription_prompt():
    qwen = next(s for s in SPECS if s.name == "vlm_qwen35")

    assert qwen.system is not None
    assert "Transcribe every character" in qwen.prompt
