"""Celery tasks — eval suite + async span ingest + rollups."""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger("agentguard.tasks")


@shared_task(name="api.tasks.execute_evaluation")
def execute_evaluation(eval_run_id: str):
    """Asynchronous Celery task that triggers the evaluation suite."""
    from api.eval_engine.runner import run_eval_suite

    logger.info("Starting background evaluation task for EvalRun %s", eval_run_id)
    run_eval_suite(eval_run_id)
    logger.info("Background evaluation task for EvalRun %s finished", eval_run_id)


@shared_task(
    bind=True,
    name="api.tasks.ingest_span_task",
    max_retries=3,
    default_retry_delay=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def ingest_span_task(self, span_payload: dict, received_at=None):
    """Idempotent span write — same logic as sync SpanIngestView."""
    from api.span_service import SpanValidationError, upsert_span

    try:
        result = upsert_span(span_payload or {})
        logger.debug(
            "ingest_span_task ok trace=%s span=%s created=%s",
            result.trace_id,
            result.span_id,
            result.created,
        )
        return {
            "trace_id": str(result.trace_id),
            "span_id": str(result.span_id),
            "created": result.created,
        }
    except SpanValidationError as exc:
        logger.warning("ingest_span_task validation failed: %s", exc)
        return {"error": str(exc)}


@shared_task(name="api.tasks.rollup_agent_metrics")
def rollup_agent_metrics(hour_iso=None):
    """Hourly aggregation into AgentMetricRollup."""
    from api.agent_models import AgentMetricRollup, Span

    if hour_iso:
        hour = parse_datetime(hour_iso)
        if hour is None:
            raise ValueError(f"Invalid hour_iso: {hour_iso}")
    else:
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        hour = now - timedelta(hours=1)

    hour_end = hour + timedelta(hours=1)
    rows = (
        Span.objects.filter(created_at__gte=hour, created_at__lt=hour_end)
        .values("agent_name")
        .annotate(
            span_count=Count("span_id"),
            failure_count=Count(
                "span_id", filter=Q(status__in=["FAILED", "TIMEOUT"])
            ),
            avg_latency_ms=Avg("latency_ms"),
            total_cost=Sum("cost"),
        )
    )
    n = 0
    for row in rows:
        AgentMetricRollup.objects.update_or_create(
            agent_name=row["agent_name"],
            hour=hour,
            defaults={
                "span_count": row["span_count"] or 0,
                "failure_count": row["failure_count"] or 0,
                "avg_latency_ms": float(row["avg_latency_ms"] or 0.0),
                "total_cost": float(row["total_cost"] or 0.0),
            },
        )
        n += 1
    logger.info("rollup_agent_metrics hour=%s agents=%s", hour.isoformat(), n)
    return {"hour": hour.isoformat(), "agents": n}


@shared_task(name="api.tasks.flush_stale_runs")
def flush_stale_runs(minutes: int = 30):
    """Mark RUNNING runs older than N minutes as TIMEOUT."""
    from api.agent_models import AgentRun

    cutoff = timezone.now() - timedelta(minutes=minutes)
    updated = AgentRun.objects.filter(status="RUNNING", started_at__lt=cutoff).update(
        status="TIMEOUT",
        ended_at=timezone.now(),
    )
    logger.info("flush_stale_runs marked %s runs TIMEOUT", updated)
    return {"updated": updated}
