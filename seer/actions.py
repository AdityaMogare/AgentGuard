"""Internal action adapters — wrap existing MCP / Splunk surfaces."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server import anomaly, splunk_client  # noqa: E402
from mcp_server.server import (  # noqa: E402
    _django_list_agents,
    _django_trace_spans,
    query_rows,
)


def failure_rates(minutes: int = 60) -> Dict[str, Any]:
    return anomaly.failure_rate_analysis(minutes=minutes)


def search_failures(
    agent_name: Optional[str] = None,
    minutes: int = 60,
) -> Dict[str, Any]:
    clauses = [splunk_client.trace_base_filter(), 'status="FAILED" OR status="TIMEOUT"']
    if agent_name:
        clauses.append(f'agent_name="{agent_name}"')
    spl = f"search {' '.join(clauses)} earliest=-{minutes}m | head 50"

    def django_fb():
        return _django_list_agents(agent_name=agent_name, status="FAILED")

    try:
        return query_rows(spl, django_fallback=django_fb)
    except splunk_client.SearchError as exc:
        return {"source": "error", "results": [], "spl": spl, "error": str(exc)}


def explain_failure(trace_id: str) -> Dict[str, Any]:
    spl = (
        f'search {splunk_client.trace_base_filter()} trace_id="{trace_id}" '
        "| sort -timestamp | head 50"
    )

    def django_fb():
        return _django_trace_spans(trace_id)

    try:
        payload = query_rows(spl, django_fallback=django_fb, count=50)
        rows = payload["results"]
        failed = [r for r in rows if r.get("status") in ("FAILED", "TIMEOUT")]
        return {
            "trace_id": trace_id,
            "source": payload["source"],
            "spl": spl,
            "spans": rows,
            "failed_spans": failed,
            "summary": (
                f"{len(failed)} failed span(s) in trace"
                if failed
                else "No failures found in trace"
            ),
            "warning": payload.get("warning"),
        }
    except splunk_client.SearchError as exc:
        return {"trace_id": trace_id, "error": str(exc), "spans": [], "failed_spans": []}


def run_anomaly(agent_name: Optional[str] = None, minutes: int = 60) -> Dict[str, Any]:
    return anomaly.anomaly_detection(agent_name=agent_name, minutes=minutes)


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)
