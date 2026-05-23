import logging
import uuid
from django.db.models import Avg, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from .agent_models import AgentRun, Span
from .agent_serializers import AgentRunSerializer, SpanSerializer
from .permissions import AgentGuardAPIKeyPermission

logger = logging.getLogger("agentguard.views")


class SpanIngestView(APIView):
    """Ingest span events from the AgentGuard SDK backend exporter."""

    permission_classes = [AgentGuardAPIKeyPermission]

    def post(self, request, *args, **kwargs):
        payload = request.data or {}
        event = payload.get("span") or payload
        required = ("trace_id", "span_id", "agent_name", "status")
        missing = [f for f in required if not event.get(f)]
        if missing:
            return Response(
                {"error": f"Missing required span fields: {missing}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            trace_id = uuid.UUID(str(event["trace_id"]))
            span_id = uuid.UUID(str(event["span_id"]))
            parent_span_id = (
                uuid.UUID(str(event["parent_span_id"]))
                if event.get("parent_span_id")
                else None
            )
        except ValueError as exc:
            return Response(
                {"error": f"Invalid UUID: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        agent_run, created = AgentRun.objects.get_or_create(
            trace_id=trace_id,
            defaults={
                "agent_name": event["agent_name"],
                "status": "RUNNING",
            },
        )

        if created is False and agent_run.agent_name != event["agent_name"]:
            agent_run.agent_name = event["agent_name"]
            agent_run.save(update_fields=["agent_name"])

        span, span_created = Span.objects.update_or_create(
            span_id=span_id,
            defaults={
                "agent_run": agent_run,
                "parent_span_id": parent_span_id,
                "agent_name": event.get("agent_name", agent_run.agent_name),
                "action_type": event.get("action_type", "observe"),
                "tool_name": event.get("tool_name"),
                "status": event.get("status", "SUCCESS"),
                "error_type": event.get("error_type"),
                "latency_ms": float(event.get("latency_ms", 0.0)),
                "input_data": event.get("input") or event.get("input_data") or {},
                "output": str(event.get("output", ""))[:8000],
                "prompt_tokens": int(event.get("prompt_tokens", 0)),
                "completion_tokens": int(event.get("completion_tokens", 0)),
                "cost": float(event.get("cost", 0.0)),
            },
        )

        # Update run aggregates
        spans = Span.objects.filter(agent_run=agent_run)
        failed = spans.filter(status__in=["FAILED", "TIMEOUT"]).count()
        run_status = "FAILED" if failed else "SUCCESS"
        if spans.filter(status="RUNNING").exists():
            run_status = "RUNNING"

        agent_run.span_count = spans.count()
        agent_run.failed_span_count = failed
        agent_run.status = run_status
        if run_status in ("SUCCESS", "FAILED"):
            agent_run.ended_at = timezone.now()
        agent_run.save()

        return Response(
            {
                "message": "Span ingested",
                "trace_id": str(trace_id),
                "span_id": str(span_id),
                "created": span_created,
            },
            status=status.HTTP_201_CREATED,
        )


class AgentRunViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve agent runs with span trees."""

    queryset = AgentRun.objects.all()
    serializer_class = AgentRunSerializer
    lookup_field = "trace_id"

    def list(self, request, *args, **kwargs):
        runs = self.get_queryset()[:100]
        data = []
        for run in runs:
            stats = Span.objects.filter(agent_run=run).aggregate(
                avg_latency=Avg("latency_ms"),
                total_cost=Sum("cost"),
            )
            serialized = self.get_serializer(run).data
            serialized["analytics"] = {
                "avg_latency_ms": round(stats["avg_latency"] or 0.0, 2),
                "total_cost": round(float(stats["total_cost"] or 0.0), 6),
                "span_count": run.span_count,
                "failed_span_count": run.failed_span_count,
            }
            data.append(serialized)
        return Response(data)

    def retrieve(self, request, *args, **kwargs):
        run = self.get_object()
        serialized = self.get_serializer(run).data
        spans = SpanSerializer(
            Span.objects.filter(agent_run=run).order_by("created_at"),
            many=True,
        ).data
        serialized["spans"] = spans
        return Response(serialized)
