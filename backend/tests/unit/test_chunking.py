"""Chunk boundaries, page attribution and section tracking.

The tokenizer is patched out so sizes come from the deterministic heuristic
(~4 Latin characters per token), which keeps these tests offline and exact.
"""
from __future__ import annotations

import pytest

from rag import chunking
from rag.chunking import _is_heading, chunk_text

SENTENCE = "The tenant shall pay the annual rent on the first day of each month. "
# One long line of ~200 heuristic tokens: a chunk on its own, never a heading.
BODY = SENTENCE * 12
# ~35 tokens: under MIN_TOKENS (64), over the 16-token tail floor.
FRAGMENT = "Signed and witnessed by both parties on the date written above in full."


@pytest.fixture(autouse=True)
def _no_tokenizer(monkeypatch):
    monkeypatch.setattr(chunking, "get_tokenizer", lambda: None)


def test_the_first_chunk_of_page_two_starts_on_page_two():
    drafts = chunk_text("\f".join([BODY, BODY]))
    assert [(d.page_start, d.page_end) for d in drafts] == [(1, 1), (2, 2)]


def test_overlap_never_carries_text_across_a_page_break():
    page_two = "Clause two. " + SENTENCE * 12
    drafts = chunk_text("\f".join([BODY, page_two]))
    assert drafts[1].text.startswith("Clause two.")


def test_a_single_page_does_not_emit_its_own_overlap_as_a_tail_chunk():
    drafts = chunk_text(BODY)
    assert len(drafts) == 1


def test_a_heading_persists_to_the_next_page_until_a_new_one_appears():
    pages = [
        "INTRODUCTION\n" + BODY,
        BODY,
        "2. Payment Terms\n" + BODY,
    ]
    drafts = chunk_text("\f".join(pages))
    assert [d.section_path for d in drafts] == [
        "INTRODUCTION", "INTRODUCTION", "2. Payment Terms",
    ]


def test_a_small_fragment_crosses_the_page_break_with_its_own_page_start():
    drafts = chunk_text("\f".join([BODY, FRAGMENT, BODY]))
    assert [(d.page_start, d.page_end) for d in drafts] == [(1, 1), (2, 3)]
    assert drafts[1].text.startswith("Signed and witnessed")


def test_the_tail_chunk_carries_section_and_meta():
    drafts = chunk_text("\f".join(["INTRODUCTION\n" + BODY, FRAGMENT]))
    tail = drafts[-1]
    assert tail.text == FRAGMENT
    assert (tail.page_start, tail.page_end) == (2, 2)
    assert tail.section_path == "INTRODUCTION"
    assert "arabic_ratio" in tail.meta


@pytest.mark.parametrize("line", [
    "Page 3 of 12", "page 3", "صفحة ٣", "الصفحة 3 من 12", "- 3 -", "12", "3 / 12",
    "12.03.2024 10:45", "ab", "(1)",
])
def test_page_numbers_and_numeric_noise_are_not_headings(line):
    assert not _is_heading(line, block_lines=3)


@pytest.mark.parametrize("line", [
    "INTRODUCTION", "2. Payment Terms", "3.1.2 Fees", "الفصل الأول", "Scope of Work",
])
def test_real_headings_are_detected(line):
    assert _is_heading(line, block_lines=3)


def test_a_short_one_line_block_is_not_a_heading():
    assert not _is_heading("Scope of Work", block_lines=1)
    # Numbered and upper-case headings do not need a following line.
    assert _is_heading("2. Payment Terms", block_lines=1)


@pytest.mark.parametrize("line", ["الْفَصْلُ الأَوَّلُ: التَّعْرِيفَاتُ", "مُقَدِّمَةٌ عامة"])
def test_vocalised_arabic_headings_are_detected(line):
    assert _is_heading(line, block_lines=3)


def test_the_tokenizer_is_loaded_once_even_from_concurrent_threads(monkeypatch):
    import threading
    import time

    import tokenizers

    loads = []

    class SlowTokenizer:
        @staticmethod
        def from_pretrained(name):
            loads.append(name)
            time.sleep(0.2)
            return object()

    # Undo this module's autouse patch: this test wants the real loader.
    monkeypatch.undo()
    monkeypatch.setattr(tokenizers, "Tokenizer", SlowTokenizer)
    monkeypatch.setattr(chunking, "_tokenizer", None)
    monkeypatch.setattr(chunking, "_tokenizer_failed", False)

    threads = [threading.Thread(target=chunking.get_tokenizer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert loads == ["BAAI/bge-m3"]
