"""Per-user data isolation.

Only Document and Conversation carry an owner; everything else (OCR batches and
runs, revisions, correction jobs, metadata, chunks) is reached through its
document, so filtering on `document__owner` is sufficient.

Another user's resource is reported as 404, never 403, so the API does not
confirm that it exists.
"""
from __future__ import annotations

from django.http import Http404, JsonResponse

from documents.models import Document

# Statuses at which a document has searchable chunks.
CHAT_READY_STATUSES = ("ready", "indexed")


def owned_documents(user):
    """`user` may be a User or a user id."""
    return Document.objects.filter(owner=user)


def get_owned_document(user, document_id) -> Document:
    document = owned_documents(user).filter(id=document_id).first()
    if document is None:
        raise Http404("no such document")
    return document


async def authenticated_user(request):
    """The logged-in user for a plain async view, or None.

    The SSE endpoints are plain Django views (not DRF), so they get no
    permission classes and must check the session themselves.
    """
    user = await request.auser()
    return user if user.is_authenticated else None


def unauthorized() -> JsonResponse:
    response = JsonResponse({"detail": "authentication required"}, status=401)
    response["WWW-Authenticate"] = 'Session realm="api"'
    return response
