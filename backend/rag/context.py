"""The contextual header a chunk is embedded with.

An isolated clause ("the amount is due within thirty days") is hard to find
because nothing in it says which document or section it belongs to. Prepending
a short header — document title, one-line summary, section — at EMBED time is
the cheapest known fix, and the stored chunk text stays raw so citations and
quotes are unaffected.

Keep the header short and topical: long, entity-heavy prefixes measurably hurt
dense retrieval, and every token spent on the header is one fewer for the text.
"""
from __future__ import annotations

from rag.chunking import count_tokens

HEADER_MAX_TOKENS = 80
SUMMARY_CHARS = 200
_TERMINAL = (".", "!", "?", ":", "،", "۔", "؟", ";", "؛")


def _sentence(*parts: str) -> str:
    """Join non-empty parts as sentences, without doubling terminal punctuation."""
    out: list[str] = []
    for part in parts:
        part = " ".join((part or "").split())
        if not part:
            continue
        out.append(part if part.endswith(_TERMINAL) else part + ".")
    return " ".join(out)


def build_context_header(
    doc_title: str,
    doc_summary: str,
    section_path: str,
    section_summary: str = "",
    *,
    max_tokens: int = HEADER_MAX_TOKENS,
) -> str:
    """`Document: <title>. <summary>\\nSection: <path>. <section summary>`, capped."""
    summary = (doc_summary or "")[:SUMMARY_CHARS]

    def render(summary_text: str, section_summary_text: str) -> str:
        lines = []
        document = _sentence(doc_title, summary_text)
        if document:
            lines.append(f"Document: {document}")
        section = _sentence(section_path, section_summary_text)
        if section:
            lines.append(f"Section: {section}")
        return "\n".join(lines)

    header = render(summary, section_summary)
    # Trim the summaries first (they are the soft parts); title and section
    # path are the identity of the chunk and go last.
    while header and count_tokens(header) > max_tokens:
        if section_summary:
            section_summary = section_summary[: max(0, len(section_summary) - 40)].rstrip()
        elif summary:
            summary = summary[: max(0, len(summary) - 40)].rstrip()
        else:
            header = render("", "")
            break
        header = render(summary, section_summary)
    return header


def embed_input(header: str, text: str) -> str:
    """What is actually sent to the embedding model."""
    return f"{header}\n\n{text}" if header else text


def build_context_text(section_path: str, section_summary: str) -> str:
    """The section context indexed for lexical search (weight B)."""
    return _sentence(section_path, section_summary)
