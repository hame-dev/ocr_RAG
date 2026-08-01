from __future__ import annotations

from django.http import Http404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from documents.models import Document
from rag.search import format_hits, hybrid_search


@api_view(["POST"])
def index_document_view(request, document_id):
    document = Document.objects.filter(id=document_id).first()
    if not document:
        raise Http404("no such document")
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
    query = request.data.get("query", "")
    if not query.strip():
        return Response({"detail": "query is required"}, status=status.HTTP_400_BAD_REQUEST)

    hits = hybrid_search(
        query,
        top_k=int(request.data.get("top_k", 8)),
        doc_ids=request.data.get("doc_ids"),
        doc_type=request.data.get("doc_type"),
        lang=request.data.get("lang"),
    )
    return Response({"query": query, "count": len(hits), "results": format_hits(hits)})
