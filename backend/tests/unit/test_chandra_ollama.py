"""Parsing Chandra-on-Ollama's layout HTML.

All fixtures are real responses from fredrezones55/chandra-ocr-2:latest. The
box convention (0..1000 on both axes) was established by probing 400x1000 and
1600x500 rasters, so these coordinates are load-bearing — if the scale were
pixels instead, every overlay would be wrong on non-1000px pages.
"""
import pytest

from ocr.engines.chandra import parse_layout_html


REAL_RESPONSE = (
    '<div data-bbox="43 217 190 276" data-label="Text"><p>INVOICE REF/2026/AR-0417</p></div>'
    '<div data-bbox="43 455 134 511" data-label="Text"><p>Total: SAR 12,500</p></div>'
    '<div data-bbox="43 679 148 731" data-label="Text"><p>Al-Noor Trading Ltd</p></div>'
)


def test_parses_text_and_boxes_from_a_real_response():
    blocks = parse_layout_html(REAL_RESPONSE)

    assert [b["text"] for b in blocks] == [
        "INVOICE REF/2026/AR-0417",
        "Total: SAR 12,500",
        "Al-Noor Trading Ltd",
    ]
    assert all(b["label"] == "Text" for b in blocks)


def test_boxes_are_normalized_from_the_0_1000_scale():
    first = parse_layout_html(REAL_RESPONSE)[0]["bbox"]

    assert first is not None
    # 43/1000, 217/1000, 190/1000, 276/1000
    assert first.as_list() == pytest.approx([0.043, 0.217, 0.190, 0.276], abs=1e-6)
    # Normalized boxes must stay inside the page.
    assert all(0.0 <= v <= 1.0 for v in first.as_list())


def test_arabic_block_survives_with_its_box():
    raw = '<div data-bbox="100 200 900 260" data-label="Text"><p>شركة النور للتجارة المحدودة</p></div>'

    blocks = parse_layout_html(raw)

    assert blocks[0]["text"] == "شركة النور للتجارة المحدودة"
    assert blocks[0]["bbox"].as_list() == pytest.approx([0.1, 0.2, 0.9, 0.26])


def test_non_text_blocks_are_skipped():
    raw = (
        '<div data-bbox="0 0 100 100" data-label="Picture"><p>a photo</p></div>'
        '<div data-bbox="0 200 100 300" data-label="Text"><p>keep me</p></div>'
    )

    assert [b["text"] for b in parse_layout_html(raw)] == ["keep me"]


def test_unparseable_box_keeps_the_text():
    """Losing a coordinate must never lose the transcription."""
    raw = '<div data-bbox="not numbers" data-label="Text"><p>still important</p></div>'

    blocks = parse_layout_html(raw)

    assert blocks[0]["text"] == "still important"
    assert blocks[0]["bbox"] is None


def test_inverted_box_is_rejected_but_text_kept():
    raw = '<div data-bbox="900 500 100 200" data-label="Text"><p>backwards box</p></div>'

    blocks = parse_layout_html(raw)

    assert blocks[0]["text"] == "backwards box"
    assert blocks[0]["bbox"] is None


def test_table_markup_becomes_readable_text():
    raw = (
        '<div data-bbox="10 10 900 200" data-label="Table">'
        "<table><tr><th>البند</th><th>Amount</th></tr>"
        "<tr><td>الإيجار السنوي</td><td>12,500</td></tr></table></div>"
    )

    text = parse_layout_html(raw)[0]["text"]

    assert "البند" in text and "12,500" in text


def test_plain_text_response_is_not_discarded():
    """The model occasionally answers without any divs."""
    blocks = parse_layout_html("just some text, no markup at all")

    assert blocks[0]["text"] == "just some text, no markup at all"
    assert blocks[0]["bbox"] is None


def test_truncated_final_div_does_not_lose_earlier_blocks():
    truncated = REAL_RESPONSE + '<div data-bbox="43 800 148 850" data-label="Text"><p>cut off'

    assert len(parse_layout_html(truncated)) == 3


def test_empty_response_yields_no_blocks():
    assert parse_layout_html("") == []
