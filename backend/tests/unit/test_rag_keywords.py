"""Per-chunk keyword extraction (statistical, no LLM) for Arabic and English."""
from __future__ import annotations

import pytest

from rag.keywords import extract_keywords, keywords_text

EN = (
    "This lease agreement sets the annual rent at twelve thousand dollars, payable in "
    "monthly instalments. The security deposit is refundable at the end of the lease "
    "term provided the tenant has met every obligation described in the agreement."
)
AR = (
    "يحدد عقد الإيجار هذا القيمة الإيجارية السنوية بمبلغ اثني عشر ألف ريال تدفع على أقساط "
    "شهرية. يُرد مبلغ التأمين في نهاية مدة الإيجار بشرط التزام المستأجر بجميع الالتزامات "
    "الواردة في العقد."
)
STOPWORDS = {"في", "من", "على", "the", "of", "is", "and", "at"}


def test_english_keywords_are_topical_and_free_of_stopwords():
    keywords = extract_keywords(EN, "en")
    assert 1 <= len(keywords) <= 8
    assert any("rent" in k or "lease" in k or "deposit" in k for k in keywords)
    assert not any(k.lower() in STOPWORDS for k in keywords)


def test_arabic_keywords_are_topical_and_free_of_stopwords():
    keywords = extract_keywords(AR, "ar")
    assert 1 <= len(keywords) <= 8
    assert any("إيجار" in k or "الإيجار" in k or "التأمين" in k for k in keywords)
    assert not any(k in STOPWORDS for k in keywords)
    assert not any(any(w in STOPWORDS for w in k.split()) for k in keywords)


def test_mixed_text_yields_keywords_from_both_scripts():
    keywords = extract_keywords(f"{EN}\n{AR}", "mixed")
    assert any(any("؀" <= ch <= "ۿ" for ch in k) for k in keywords)
    assert any(k.isascii() for k in keywords)
    assert len(keywords) <= 8


def test_noise_tokens_are_filtered():
    keywords = extract_keywords("2024 12 45 rent 7.5 ab", "en")
    assert all(any(ch.isalpha() for ch in k) for k in keywords)
    assert "ab" not in keywords


def test_empty_text_gives_no_keywords():
    assert extract_keywords("", "en") == []
    assert extract_keywords("   ", "ar") == []


def test_keywords_text_joins_for_lexical_search():
    assert keywords_text(["annual rent", "security deposit"]) == "annual rent; security deposit"
    assert keywords_text([]) == ""


def test_a_missing_yake_fails_loudly_instead_of_returning_no_keywords(monkeypatch):
    """An image without yake must not index a library with empty keywords:
    the ImportError propagates so the index task fails and says why."""
    import sys

    from rag import keywords

    monkeypatch.setattr(keywords, "_extractors", {})
    monkeypatch.setitem(sys.modules, "yake", None)
    with pytest.raises(ImportError):
        keywords.extract_keywords("The tenant pays the annual rent monthly.", "en")
