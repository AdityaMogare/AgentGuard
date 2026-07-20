from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import (
    TraceIngestView,
    PromptVersionViewSet,
    DatasetViewSet,
    EvalRunViewSet,
    CompareRunsView,
)
from .agent_views import SpanIngestView, SpanIngestBatchView, AgentRunViewSet
from .alert_views import AlertWebhookView
from .auth_views import SDKKeyListCreateView, SDKKeyRevokeView
from .health_views import HealthView

# Use DRF Default Router to register ViewSets
router = DefaultRouter()
router.register(r'prompts', PromptVersionViewSet, basename='prompt')
router.register(r'datasets', DatasetViewSet, basename='dataset')
router.register(r'eval-runs', EvalRunViewSet, basename='eval-run')
router.register(r'agents', AgentRunViewSet, basename='agent')

urlpatterns = [
    # Health
    path('health/', HealthView.as_view(), name='health'),
    # Auth — JWT + SDK keys
    path('auth/token/', TokenObtainPairView.as_view(), name='token-obtain'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('auth/keys/', SDKKeyListCreateView.as_view(), name='sdk-keys'),
    path('auth/keys/<uuid:key_id>/', SDKKeyRevokeView.as_view(), name='sdk-key-revoke'),
    # AgentGuard span ingest (SDK backend exporter)
    path('spans/ingest/', SpanIngestView.as_view(), name='span-ingest'),
    path('spans/ingest/batch/', SpanIngestBatchView.as_view(), name='span-ingest-batch'),
    # Splunk alert webhook
    path('alerts/webhook/', AlertWebhookView.as_view(), name='alert-webhook'),
    # Legacy PromptOps (deprecated — kept for reference; use AgentGuard spans)
    path('traces/ingest/', TraceIngestView.as_view(), name='trace-ingest'),
    # Side-by-Side Run Comparison Endpoint
    path('eval-runs/compare/', CompareRunsView.as_view(), name='eval-runs-compare'),
    # Include Router-generated endpoints
    path('', include(router.urls)),
]
