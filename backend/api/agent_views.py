"""AgentGuard span ingest + agent run APIs."""
from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import Avg, Sum
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .agent_models import AgentRun, Span
from .agent_serializers import AgentRunSerializer, SpanSerializer
from .authentication import JWTAuthentication, SDKKeyAuthentication
from .permissions import HasScopeAgentsRead, HasScopeSpansWrite
from .span_service import SpanValidationError, parse_span_event, upsert_span

logger = logging.getLogger("agentguard.views")


class AgentRunPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class SpanIngestView(APIView):
    """Ingest span events from the AgentGuard SDK backend exporter."""

    authentication_classes = [c for c in [SDKKeyAuthentication] if c]
    permission_classes = [HasScopeSpansWrite]

    def post(self, request, *args, **kwargs):
        payload = request.data or {}
        event = parse_span_event(payload if isinstance(payload, dict) else {})

        if getattr(settings, "ASYNC_SPAN_INGEST", False):
            try:
                # Validate before enqueue so clients get 400, not silent worker failure
                from .span_service import validate_and_parse_ids

                validate_and_parse_ids(event)
            except SpanValidationError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            from .tasks import ingest_span_task

            ingest_span_task.delay(event)
            return Response(
                {
                    "message": "accepted",
                    "async": True,
                    "trace_id": str(event.get("trace_id")),
                    "span_id": str(event.get("span_id")),
                },
                status=status.HTTP_202_ACCEPTED,
            )

        try:
            result = upsert_span(event)
        except SpanValidationError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "message": "Span ingested",
                "trace_id": str(result.trace_id),
                "span_id": str(result.span_id),
                "created": result.created,
            },
            status=status.HTTP_201_CREATED,
        )


class SpanIngestBatchView(APIView):
    """Batch ingest (max 100). Async when ASYNC_SPAN_INGEST=1."""

    authentication_classes = [c for c in [SDKKeyAuthentication] if c]
    permission_classes = [HasScopeSpansWrite]

    def post(self, request, *args, **kwargs):
        payload = request.data or {}
        spans = payload.get("spans") if isinstance(payload, dict) else None
        if not isinstance(spans, list):
            return Response({"error": "Expected {\"spans\": [...]}"}, status=400)
        if len(spans) > 100:
            return Response({"error": "Max 100 spans per batch"}, status=400)

        if getattr(settings, "ASYNC_SPAN_INGEST", False):
            from .tasks import ingest_span_task

            for event in spans:
                ingest_span_task.delay(event if isinstance(event, dict) else {})
            return Response(
                {"message": "accepted", "async": True, "count": len(spans)},
                status=status.HTTP_202_ACCEPTED,
            )

        results = []
        errors = []
        for i, event in enumerate(spans):
            try:
                r = upsert_span(event if isinstance(event, dict) else {})
                results.append(
                    {
                        "trace_id": str(r.trace_id),
                        "span_id": str(r.span_id),
                        "created": r.created,
                    }
                )
            except SpanValidationError as exc:
                errors.append({"index": i, "error": str(exc)})
        return Response(
            {"message": "Batch ingested", "results": results, "errors": errors},
            status=status.HTTP_201_CREATED if results else status.HTTP_400_BAD_REQUEST,
        )


class AgentRunViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve agent runs with span trees."""

    serializer_class = AgentRunSerializer
    lookup_field = "trace_id"
    pagination_class = AgentRunPagination
    authentication_classes = [
        c for c in [JWTAuthentication, SDKKeyAuthentication] if c
    ]
    permission_classes = [HasScopeAgentsRead]

    def get_queryset(self):
        qs = AgentRun.objects.annotate(
            avg_latency=Avg("spans__latency_ms"),
            total_cost=Sum("spans__cost"),
        ).order_by("-started_at")

        agent_name = self.request.query_params.get("agent_name")
        run_status = self.request.query_params.get("status")
        since = self.request.query_params.get("since")
        if agent_name:
            qs = qs.filter(agent_name=agent_name)
        if run_status:
            qs = qs.filter(status=run_status)
        if since:
            dt = parse_datetime(since)
            if dt:
                qs = qs.filter(started_at__gte=dt)
        return qs

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        # Paginate only when client asks (?page=) — bare list keeps MCP/demo compat
        if "page" in request.query_params or "page_size" in request.query_params:
            page = self.paginate_queryset(queryset)
            runs = page
            paginate = True
        else:
            runs = list(queryset[:100])
            paginate = False

        data = []
        for run in runs:
            serialized = self.get_serializer(run).data
            serialized["analytics"] = {
                "avg_latency_ms": round(getattr(run, "avg_latency", None) or 0.0, 2),
                "total_cost": round(float(getattr(run, "total_cost", None) or 0.0), 6),
                "span_count": run.span_count,
                "failed_span_count": run.failed_span_count,
            }
            data.append(serialized)
        if paginate:
            return self.get_paginated_response(data)
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
