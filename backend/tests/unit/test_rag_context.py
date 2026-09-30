"""The contextual header prepended to a chunk at embedding time."""
from __future__ import annotations

import pytest

from rag import chunking
from rag.context import build_context_header, build_context_text, embed_input


@pytest.fixture(autouse=True)
def _heuristic_tokens(monkeypatch):
    monkeypatch.setattr(chunking, "get_tokenizer", lambda: None)


def test_header_has_document_and_section_lines():
    header = build_context_header("Lease agreement", "A one-year lease.", "2. Payment terms", "Rent is due monthly.")
    assert header == "Document: Lease agreement. A one-year lease.\nSection: 2. Payment terms. Rent is due monthly."


def test_header_omits_empty_parts():
    assert build_context_header("Lease agreement", "", "", "") == "Document: Lease agreement."
    assert build_context_header("", "", "", "") == ""
    assert build_context_header("", "", "2. Payment terms", "") == "Section: 2. Payment terms."


def test_header_does_not_duplicate_terminal_punctuation():
    header = build_context_header("Lease agreement.", "A one-year lease!", "2. Payment terms:", "")
    assert header == "Document: Lease agreement. A one-year lease!\nSection: 2. Payment terms:"


def test_header_is_capped_at_max_tokens():
    long_summary = "Rent and deposits and renewals and notices. " * 40
    header = build_context_header("Lease agreement", long_summary, "2. Payment terms", "", max_tokens=80)
    assert chunking.count_tokens(header) <= 80
    assert header.startswith("Document: Lease agreement.")
    assert "Section: 2. Payment terms." in header


def test_embed_input_prepends_header_when_present():
    assert embed_input("Document: Lease.", "The rent is 1000.") == "Document: Lease.\n\nThe rent is 1000."
    assert embed_input("", "The rent is 1000.") == "The rent is 1000."


def test_context_text_for_lexical_search():
    assert build_context_text("2. Payment terms", "Rent is due monthly.") == "2. Payment terms. Rent is due monthly."
    assert build_context_text("", "") == ""
