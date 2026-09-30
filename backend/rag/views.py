from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.ownership import get_owned_document, owned_documents
from rag.search import format_hits, hybrid_search


@api_view(["POST"])
def index_document_view(request, document_id):
    document = get_owned_document(request.user, document_id)
    if not document.current_revision:
        return Response(
            {"detail": "finalize the document text before indexing"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from rag.tasks import index_document

    index_document.delay(str(document.id))
    return Response({"status": "queued"}, status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
def search(request):
    """Debug/search endpoint. The agent uses the same hybrid_search underneath."""
    if not isinstance(request.data, dict):
        return Response({"detail": "the body must be a JSON object"}, status=status.HTTP_400_BAD_REQUEST)
    query = request.data.get("query", "")
    if not isinstance(query, str) or not query.strip():
        return Response({"detail": "query is required"}, status=status.HTTP_400_BAD_REQUEST)

    # Always an explicit list: `None` would mean "every user's documents".
    owned = owned_documents(request.user)
    requested = request.data.get("doc_ids")
    if requested:
        if not isinstance(requested, list):
            return Response(
                {"detail": "doc_ids must be a list"}, status=status.HTTP_400_BAD_REQUEST
            )
        owned = owned.filter(id__in=[str(d) for d in requested])
    doc_ids = [str(d) for d in owned.values_list("id", flat=True)]

    try:
        top_k = max(1, min(int(request.data.get("top_k", 8)), 50))
    except (TypeError, ValueError):
        return Response({"detail": "top_k must be an integer"}, status=status.HTTP_400_BAD_REQUEST)

    hits = hybrid_search(
        query,
        top_k=top_k,
        doc_ids=doc_ids,
        doc_type=request.data.get("doc_type"),
        lang=request.data.get("lang"),
    )
    return Response({"query": query, "count": len(hits), "results": format_hits(hits)})
