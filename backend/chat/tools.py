"""Agent tools.

There are NO write tools, by design. The agent can read documents, metadata and
chunks; it cannot modify a document, a revision or a metadata record. Retrieval
should never be able to mutate the corpus it retrieves from.
"""
from __future__ import annotations

import logging
from typing import Annotated

from asgiref.sync import sync_to_async
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from chat.research import research_profile
from rag.search import format_hits, hybrid_search

logger = logging.getLogger(__name__)


@tool
async def search_documents(
    query: str,
    allowed_doc_ids: Annotated[list[str] | None, InjectedState("doc_ids")],
    research_mode: Annotated[str, InjectedState("research_mode")],
    doc_type: str | None = None,
    lang: str | None = None,
) -> list[dict]:
    """Search the user's documents by meaning and by keyword at the same time.

    Works across languages: an English query will find Arabic passages and vice
    versa, so query in whichever language is most natural.

    Args:
        query: What to look for. A phrase works better than a single word.
        doc_type: Restrict by type, e.g. "contract", "invoice", "report".
        lang: Restrict by chunk language: "ar", "en" or "mixed".

    Returns:
        Passages with chunk_id, document_id, document_title, page numbers and text.
        Cite a passage with [[cite:<chunk_id>]].
    """
    if allowed_doc_ids == []:
        return []
    profile = research_profile(research_mode)
    hits = await sync_to_async(hybrid_search, thread_sensitive=True)(
        query,
        top_k=profile["top_k"],
        doc_ids=allowed_doc_ids,
        doc_type=doc_type,
        lang=lang,
    )
    return format_hits(hits, max_chars=profile["excerpt_chars"])


@tool
async def get_document_metadata(
    document_id: str,
    allowed_doc_ids: Annotated[list[str] | None, InjectedState("doc_ids")],
) -> dict:
    """Get the structured metadata record for one document.

    Use this for questions about a document as a whole — its type, its dates,
    who it involves, its reference numbers, or its summary — rather than
    searching for a passage.
    """

    @sync_to_async(thread_sensitive=True)
    def _load():
        from enrichment.models import MetadataRecord

        if not _document_is_allowed(document_id, allowed_doc_ids):
            return {"error": "document is outside this conversation's selected sources"}
        record = MetadataRecord.objects.filter(document_id=document_id).first()
        if not record:
            return {"error": f"no metadata for document {document_id}"}
        return {
            "document_id": str(record.document_id),
            "doc_type": record.doc_type,
            "title": record.title,
            "title_translit": record.title_translit,
            "languages": record.languages,
            "summary_short": record.summary_short,
            "summary_long": record.summary_long,
            "keywords": record.keywords,
            "topics": record.topics,
            "entities": record.entities,
            "dates": record.dates,
            "identifiers": record.identifiers,
            "custom_fields": record.custom_fields,
            "quality_flags": record.quality_flags,
            "is_partial": record.is_partial,
        }

    return await _load()


@tool
async def list_documents(
    allowed_doc_ids: Annotated[list[str] | None, InjectedState("doc_ids")],
    query: str | None = None,
    doc_type: str | None = None,
    lang: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """List the documents in the library.

    Use this first for questions like "what documents do I have about X" before
    searching inside their contents.
    """

    @sync_to_async(thread_sensitive=True)
    def _load():
        from documents.models import Document

        qs = Document.objects.select_related("metadata").filter(status="ready")
        if allowed_doc_ids is not None:
            qs = qs.filter(id__in=allowed_doc_ids)
        if doc_type:
            qs = qs.filter(metadata__doc_type=doc_type)
        if lang:
            qs = qs.filter(metadata__primary_language=lang)
        if query:
            qs = qs.filter(title__icontains=query) | qs.filter(
                metadata__keywords__overlap=[query]
            )
        return [
            {
                "document_id": str(d.id),
                "title": d.display_title,
                "doc_type": getattr(d, "metadata", None) and d.metadata.doc_type or "",
                "summary": getattr(d, "metadata", None) and d.metadata.summary_short or "",
                "page_count": d.page_count,
                "languages": d.detected_languages,
            }
            for d in qs.distinct()[: min(limit, 50)]
        ]

    return await _load()


@tool
async def read_document_page(
    document_id: str,
    page_number: int,
    allowed_doc_ids: Annotated[list[str] | None, InjectedState("doc_ids")],
) -> str:
    """Read the exact finalized text of one page.

    Use this to verify a quote or to read the context around something a search
    result mentioned.
    """

    @sync_to_async(thread_sensitive=True)
    def _load():
        from documents.models import Document

        if not _document_is_allowed(document_id, allowed_doc_ids):
            return "document is outside this conversation's selected sources"
        document = Document.objects.select_related("current_revision").filter(
            id=document_id
        ).first()
        if not document or not document.current_revision:
            return f"no finalized text for document {document_id}"
        pages = document.current_revision.pages
        if page_number < 1 or page_number > len(pages):
            return f"document has {len(pages)} pages; {page_number} is out of range"
        return pages[page_number - 1]

    return await _load()


@tool
async def get_chunk_context(
    chunk_id: str,
    allowed_doc_ids: Annotated[list[str] | None, InjectedState("doc_ids")],
    before: int = 1,
    after: int = 1,
) -> str:
    """Expand a retrieved passage with its neighbouring passages.

    Use when a search result looks relevant but is cut off mid-thought.
    """

    @sync_to_async(thread_sensitive=True)
    def _load():
        from rag.models import Chunk

        chunk = Chunk.objects.filter(id=chunk_id).first()
        if not chunk:
            return f"no such chunk {chunk_id}"
        if not _document_is_allowed(str(chunk.document_id), allowed_doc_ids):
            return "chunk is outside this conversation's selected sources"
        neighbours = Chunk.objects.filter(
            revision_id=chunk.revision_id,
            chunk_index__gte=chunk.chunk_index - before,
            chunk_index__lte=chunk.chunk_index + after,
        ).order_by("chunk_index")
        return "\n\n".join(c.text for c in neighbours)

    return await _load()


def _document_is_allowed(
    document_id: str, allowed_doc_ids: list[str] | None
) -> bool:
    """None means library-wide; a list is an authoritative allow-list."""
    return allowed_doc_ids is None or str(document_id) in {
        str(allowed_id) for allowed_id in allowed_doc_ids
    }


TOOLS = [
    search_documents,
    get_document_metadata,
    list_documents,
    read_document_page,
    get_chunk_context,
]
