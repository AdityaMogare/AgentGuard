from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    TraceIngestView, 
    PromptVersionViewSet, 
    DatasetViewSet, 
    EvalRunViewSet, 
    CompareRunsView
)
from .agent_views import SpanIngestView, AgentRunViewSet

# Use DRF Default Router to register ViewSets
router = DefaultRouter()
router.register(r'prompts', PromptVersionViewSet, basename='prompt')
router.register(r'datasets', DatasetViewSet, basename='dataset')
router.register(r'eval-runs', EvalRunViewSet, basename='eval-run')
router.register(r'agents', AgentRunViewSet, basename='agent')

urlpatterns = [
    # AgentGuard span ingest (SDK backend exporter)
    path('spans/ingest/', SpanIngestView.as_view(), name='span-ingest'),
    # Legacy PromptOps (deprecated — kept for reference; use AgentGuard spans)
    path('traces/ingest/', TraceIngestView.as_view(), name='trace-ingest'),
    
    # Side-by-Side Run Comparison Endpoint
    path('eval-runs/compare/', CompareRunsView.as_view(), name='eval-runs-compare'),
    
    # Include Router-generated endpoints (CRUD for prompt versions, datasets, and eval runs)
    path('', include(router.urls)),
]
