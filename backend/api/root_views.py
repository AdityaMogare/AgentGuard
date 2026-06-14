"""Minimal root view so port 8001 is clearly AgentGuard, not Splunk."""
from django.http import JsonResponse


def root(request):
    return JsonResponse(
        {
            "service": "AgentGuard API",
            "note": "Splunk Web UI runs on http://localhost:8000 — not this server.",
            "splunk_dashboard": "http://localhost:8000/en-US/app/agentguard/agentguard_overview",
            "endpoints": {
                "agents": "/api/v1/agents/",
                "span_ingest": "/api/v1/spans/ingest/",
                "alert_webhook": "/api/v1/alerts/webhook/",
                "admin": "/admin/",
            },
        }
    )
