from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    TraceIngestView, 
    PromptVersionViewSet, 
    DatasetViewSet, 
    EvalRunViewSet, 
    CompareRunsView
)

# Use DRF Default Router to register ViewSets
router = DefaultRouter()
router.register(r'prompts', PromptVersionViewSet, basename='prompt')
router.register(r'datasets', DatasetViewSet, basename='dataset')
router.register(r'eval-runs', EvalRunViewSet, basename='eval-run')

urlpatterns = [
    # Trace Ingest Endpoint (called by SDK client)
    path('traces/ingest/', TraceIngestView.as_view(), name='trace-ingest'),
    
    # Side-by-Side Run Comparison Endpoint
    path('eval-runs/compare/', CompareRunsView.as_view(), name='eval-runs-compare'),
    
    # Include Router-generated endpoints (CRUD for prompt versions, datasets, and eval runs)
    path('', include(router.urls)),
]
