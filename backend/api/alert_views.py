import logging
import uuid

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .agent_models import AgentAlert
from .permissions import AgentGuardAPIKeyPermission

logger = logging.getLogger("agentguard.alerts")


def _extract_field(payload: dict, *keys, default=""):
    for key in keys:
        val = payload.get(key)
        if val not in (None, ""):
            return val
    result = payload.get("result") or {}
    if isinstance(result, dict):
        for key in keys:
            val = result.get(key)
            if val not in (None, ""):
                return val
    return default


class AlertWebhookView(APIView):
    """
    Receive Splunk alert webhooks (or demo POSTs).
    Splunk webhook action should POST JSON to /api/v1/alerts/webhook/
    """

    permission_classes = [AgentGuardAPIKeyPermission]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        alerts = AgentAlert.objects.all()[:50]
        return Response(
            [
                {
                    "alert_id": str(a.alert_id),
                    "alert_name": a.alert_name,
                    "severity": a.severity,
                    "agent_name": a.agent_name,
                    "trace_id": str(a.trace_id) if a.trace_id else None,
                    "message": a.message,
                    "received_at": a.received_at.isoformat(),
                    "acknowledged": a.acknowledged,
                }
                for a in alerts
            ]
        )

    def post(self, request, *args, **kwargs):
        payload = request.data or {}
        alert_name = str(
            _extract_field(payload, "alert_name", "search_name", "name")
            or "agentguard_alert"
        )
        severity = str(_extract_field(payload, "severity", "alert_severity") or "medium")
        agent_name = str(_extract_field(payload, "agent_name", "agent") or "")
        trace_raw = _extract_field(payload, "trace_id", "traceId")
        trace_id = None
        if trace_raw:
            try:
                trace_id = uuid.UUID(str(trace_raw))
            except ValueError:
                trace_id = None

        message = str(
            _extract_field(payload, "message", "description", "reason")
            or f"Alert {alert_name} fired"
        )

        alert = AgentAlert.objects.create(
            alert_name=alert_name,
            severity=severity,
            agent_name=agent_name,
            trace_id=trace_id,
            message=message,
            payload=payload,
        )
        logger.info("Alert received: %s severity=%s agent=%s", alert_name, severity, agent_name)

        return Response(
            {
                "message": "Alert received",
                "alert_id": str(alert.alert_id),
                "alert_name": alert_name,
                "severity": severity,
            },
            status=status.HTTP_201_CREATED,
        )
