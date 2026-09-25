from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from chat import attachments as chat_attachments
from chat import sse_views as chat_sse
from chat import views as chat_views
from common import health
from correction import views as correction_views
from documents import sse_views as doc_sse
from documents import views as doc_views
from enrichment import views as enrich_views
from ocr import views as ocr_views
from rag import views as rag_views

router = DefaultRouter()
router.register(r"documents", doc_views.DocumentViewSet, basename="document")
router.register(r"ocr/batches", ocr_views.OCRBatchViewSet, basename="ocrbatch")
router.register(r"ocr/runs", ocr_views.OCRRunViewSet, basename="ocrrun")
router.register(r"conversations", chat_views.ConversationViewSet, basename="conversation")

urlpatterns = [
    # --- SSE. Plain async Django views, deliberately not DRF. ---------------
    # Keep these before router.urls: the DocumentViewSet exposes a polling
    # fallback at the same `events/` path, and Django resolves first match.
    path("api/documents/<uuid:document_id>/events/", doc_sse.document_events),
    path("api/conversations/<uuid:conversation_id>/stream/", chat_sse.chat_stream),

    # --- Chat attachments (General mode) ------------------------------------
    path("api/chat/attachments/", chat_attachments.upload_attachment),
    path("api/chat/attachments/<uuid:attachment_id>/", chat_attachments.delete_attachment),
    path("api/chat/attachments/<uuid:attachment_id>/preview/", chat_attachments.attachment_preview),

    # --- Auth. Session cookie + CSRF; accounts are operator-created. -------
    path("api/auth/", include("accounts.urls")),

    path("api/", include(router.urls)),

    # --- OCR ----------------------------------------------------------------
    path("api/ocr/engines/", ocr_views.engine_catalog),
    path("api/documents/<uuid:document_id>/ocr/", ocr_views.start_ocr),

    # --- Correction ---------------------------------------------------------
    path("api/documents/<uuid:document_id>/ai-correct/", correction_views.start_correction),
    path("api/ai-corrections/<uuid:job_id>/", correction_views.correction_job),
    path("api/ai-corrections/<uuid:job_id>/apply/", correction_views.apply_correction),

    # --- Enrichment ---------------------------------------------------------
    path("api/documents/<uuid:document_id>/extraction-plan/", enrich_views.extraction_plan),
    path("api/documents/<uuid:document_id>/enrich/", enrich_views.enrich),
    path("api/documents/<uuid:document_id>/metadata/", enrich_views.metadata),
    path("api/metadata/schema/", enrich_views.metadata_schema),

    # --- RAG ----------------------------------------------------------------
    path("api/documents/<uuid:document_id>/index/", rag_views.index_document_view),
    path("api/search/", rag_views.search),

    # --- Ops ----------------------------------------------------------------
    path("api/health/", health.liveness),
    path("api/health/deep/", health.readiness),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
]
