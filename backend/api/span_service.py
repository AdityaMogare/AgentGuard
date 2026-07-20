"""Shared span upsert used by sync ingest and Celery workers."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from django.db.models import Count, Q
from django.utils import timezone

from .agent_models import AgentRun, Span


class SpanValidationError(ValueError):
    """Raised when a span payload is invalid."""


@dataclass
class IngestResult:
    trace_id: uuid.UUID
    span_id: uuid.UUID
    created: bool
    agent_run_id: uuid.UUID


def parse_span_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize request body to a span event dict."""
    return payload.get("span") or payload


def validate_and_parse_ids(
    event: Dict[str, Any],
) -> Tuple[uuid.UUID, uuid.UUID, Optional[uuid.UUID]]:
    required = ("trace_id", "span_id", "agent_name", "status")
    missing = [f for f in required if not event.get(f)]
    if missing:
        raise SpanValidationError(f"Missing required span fields: {missing}")
    try:
        trace_id = uuid.UUID(str(event["trace_id"]))
        span_id = uuid.UUID(str(event["span_id"]))
        parent_span_id = (
            uuid.UUID(str(event["parent_span_id"]))
            if event.get("parent_span_id")
            else None
        )
    except ValueError as exc:
        raise SpanValidationError(f"Invalid UUID: {exc}") from exc
    return trace_id, span_id, parent_span_id


def upsert_span(event: Dict[str, Any]) -> IngestResult:
    """
    Idempotent span write — safe for Celery retries.
    Same semantics as the original SpanIngestView.
    """
    trace_id, span_id, parent_span_id = validate_and_parse_ids(event)

    agent_run, created = AgentRun.objects.get_or_create(
        trace_id=trace_id,
        defaults={
            "agent_name": event["agent_name"],
            "status": "RUNNING",
        },
    )

    if not created and agent_run.agent_name != event["agent_name"]:
        agent_run.agent_name = event["agent_name"]
        agent_run.save(update_fields=["agent_name"])

    _span, span_created = Span.objects.update_or_create(
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

    _refresh_run_aggregates(agent_run)

    return IngestResult(
        trace_id=trace_id,
        span_id=span_id,
        created=span_created,
        agent_run_id=agent_run.trace_id,
    )


def _refresh_run_aggregates(agent_run: AgentRun) -> None:
    stats = Span.objects.filter(agent_run=agent_run).aggregate(
        total=Count("span_id"),
        failed=Count("span_id", filter=Q(status__in=["FAILED", "TIMEOUT"])),
        running=Count("span_id", filter=Q(status="RUNNING")),
    )
    total = stats["total"] or 0
    failed = stats["failed"] or 0
    running = stats["running"] or 0

    if running:
        run_status = "RUNNING"
    elif failed:
        run_status = "FAILED"
    else:
        run_status = "SUCCESS"

    agent_run.span_count = total
    agent_run.failed_span_count = failed
    agent_run.status = run_status
    update_fields = ["span_count", "failed_span_count", "status"]
    if run_status in ("SUCCESS", "FAILED"):
        agent_run.ended_at = timezone.now()
        update_fields.append("ended_at")
    agent_run.save(update_fields=update_fields)
