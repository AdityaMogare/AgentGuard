#!/usr/bin/env python3
"""
AgentGuard Splunk MCP server — query agent telemetry + AI/ML features.

Environment:
  SPLUNK_MOCK=1              Mock data (default for local dev)
  SPLUNK_MOCK=0              Real Splunk REST + Django fallback
  SPLUNK_HOST                https://localhost:8089
  SPLUNK_REST_TOKEN          Splunk session key (Splunk auth scheme)
  SPLUNK_TOKEN               Alias for SPLUNK_REST_TOKEN
  SPLUNK_AI_ASSISTANT_ENABLED=1  Enable NL→SPL via AI Assistant API
  AGENTGUARD_BACKEND_URL     http://localhost:8001 (Django fallback)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    FastMCP = None  # type: ignore

from mcp_server import ai_assistant, anomaly, splunk_client

mcp = FastMCP("agentguard-splunk") if FastMCP else None

MOCK_TRACES = anomaly.mock_traces()


def _django_backend_url() -> str:
    return os.environ.get("AGENTGUARD_BACKEND_URL", "http://localhost:8001").rstrip("/")


def _django_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    key = os.environ.get("AGENTGUARD_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Api-Key {key}"
    return headers


def _django_list_agents(
    agent_name: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    import requests

    url = f"{_django_backend_url()}/api/v1/agents/"
    resp = requests.get(url, headers=_django_headers(), timeout=10)
    resp.raise_for_status()
    rows = []
    for run in resp.json():
        if agent_name and run.get("agent_name") != agent_name:
            continue
        if status and run.get("status") != status:
            continue
        rows.append(
            {
                "trace_id": run.get("trace_id"),
                "agent_name": run.get("agent_name"),
                "status": run.get("status"),
                "error_type": None,
                "latency_ms": (run.get("analytics") or {}).get("avg_latency_ms"),
                "tool_name": None,
            }
        )
    return rows


def _django_trace_spans(trace_id: str) -> List[Dict[str, Any]]:
    import requests

    url = f"{_django_backend_url()}/api/v1/agents/{trace_id}/"
    resp = requests.get(url, headers=_django_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for span in data.get("spans", []):
        rows.append(
            {
                "trace_id": trace_id,
                "span_id": str(span.get("span_id")),
                "parent_span_id": (
                    str(span["parent_span_id"]) if span.get("parent_span_id") else None
                ),
                "agent_name": span.get("agent_name"),
                "status": span.get("status"),
                "error_type": span.get("error_type"),
                "latency_ms": span.get("latency_ms"),
                "tool_name": span.get("tool_name"),
                "action_type": span.get("action_type"),
                "output": (span.get("output") or "")[:500],
            }
        )
    return rows


def _django_health_summary() -> List[Dict[str, Any]]:
    import requests

    url = f"{_django_backend_url()}/api/v1/agents/"
    resp = requests.get(url, headers=_django_headers(), timeout=10)
    resp.raise_for_status()
    by_agent: Dict[str, Dict[str, Any]] = {}
    for run in resp.json():
        name = run.get("agent_name", "unknown")
        bucket = by_agent.setdefault(
            name,
            {"agent_name": name, "total": 0, "success": 0, "failures": 0, "error_types": []},
        )
        bucket["total"] += 1
        if run.get("status") == "SUCCESS":
            bucket["success"] += 1
        elif run.get("status") in ("FAILED", "TIMEOUT"):
            bucket["failures"] += 1
    return list(by_agent.values())


def query_rows(
    spl: str,
    *,
    django_fallback: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    count: int = 50,
) -> Dict[str, Any]:
    """Execute search with mock / Splunk / Django fallback chain."""
    if splunk_client.use_mock():
        return {"source": "mock", "results": MOCK_TRACES, "spl": spl, "warning": None}

    errors: List[str] = []
    try:
        rows, source = splunk_client.run_search(spl, count=count)
        return {"source": source, "results": rows, "spl": spl, "warning": None}
    except splunk_client.SearchError as exc:
        errors.append(f"Splunk: {exc}")

    if django_fallback:
        try:
            rows = django_fallback()
            return {
                "source": "django",
                "results": rows,
                "spl": spl,
                "warning": "; ".join(errors),
            }
        except Exception as exc:
            errors.append(f"Django: {exc}")

    raise splunk_client.SearchError("; ".join(errors))


if mcp:

    @mcp.tool()
    def search_agent_traces(
        agent_name: Optional[str] = None,
        status: Optional[str] = None,
        minutes: int = 15,
    ) -> str:
        """Search recent agent spans in Splunk. Filter by agent_name and status (SUCCESS/FAILED/TIMEOUT)."""
        clauses = [splunk_client.trace_base_filter()]
        if agent_name:
            clauses.append(f'agent_name="{agent_name}"')
        if status:
            clauses.append(f'status="{status}"')
        spl = f"search {' '.join(clauses)} earliest=-{minutes}m | head 50"

        def django_fb():
            return _django_list_agents(agent_name=agent_name, status=status)

        try:
            payload = query_rows(spl, django_fallback=django_fb)
            return json.dumps(payload, indent=2)
        except splunk_client.SearchError as exc:
            return json.dumps({"error": str(exc), "spl": spl}, indent=2)

    @mcp.tool()
    def explain_agent_failure(trace_id: str) -> str:
        """Return span details and error context for a failed agent trace_id."""
        spl = (
            f"search {splunk_client.trace_base_filter()} trace_id=\"{trace_id}\" "
            "| sort -timestamp | head 20"
        )

        def django_fb():
            return _django_trace_spans(trace_id)

        try:
            payload = query_rows(spl, django_fallback=django_fb, count=20)
            rows = payload["results"]
            failed = [r for r in rows if r.get("status") in ("FAILED", "TIMEOUT")]
            return json.dumps(
                {
                    "trace_id": trace_id,
                    "source": payload["source"],
                    "spl": spl,
                    "spans": rows,
                    "failed_spans": failed,
                    "warning": payload.get("warning"),
                    "summary": (
                        f"{len(failed)} failed span(s) in trace"
                        if failed
                        else "No failures found in trace"
                    ),
                },
                indent=2,
            )
        except splunk_client.SearchError as exc:
            return json.dumps({"error": str(exc), "trace_id": trace_id, "spl": spl}, indent=2)

    @mcp.tool()
    def agent_health_summary(minutes: int = 60) -> str:
        """Aggregate pass/fail rates and top error types by agent_name."""
        spl = (
            f"search {splunk_client.trace_base_filter()} earliest=-{minutes}m "
            "| stats count as total "
            'count(eval(status="SUCCESS")) as success '
            'count(eval(status="FAILED" OR status="TIMEOUT")) as failures '
            'values(error_type) as error_types by agent_name'
        )

        try:
            payload = query_rows(spl, django_fallback=_django_health_summary)
            return json.dumps(
                {
                    "source": payload["source"],
                    "spl": spl,
                    "agents": payload["results"],
                    "warning": payload.get("warning"),
                },
                indent=2,
            )
        except splunk_client.SearchError as exc:
            return json.dumps({"error": str(exc), "spl": spl}, indent=2)

    @mcp.tool()
    def nl_search(question: str) -> str:
        """Convert natural language to SPL and run the search. Falls back to rule-based SPL if AI Assistant is unavailable."""
        return json.dumps(ai_assistant.nl_search(question), indent=2)

    @mcp.tool()
    def anomaly_detection(agent_name: Optional[str] = None, minutes: int = 60) -> str:
        """Detect latency/event anomalies using MLTK DensityFunction or built-in anomalydetection."""
        return json.dumps(anomaly.anomaly_detection(agent_name, minutes), indent=2)

    @mcp.tool()
    def failure_rate_analysis(minutes: int = 60) -> str:
        """Compute failure rates and error types per agent over a time window."""
        return json.dumps(anomaly.failure_rate_analysis(minutes), indent=2)

    @mcp.tool()
    def alert_summary(hours: int = 24) -> str:
        """Summarize fired AgentGuard Splunk alerts (or FAILED-span proxy when alert log unavailable)."""
        return json.dumps(anomaly.alert_summary(hours), indent=2)

    @mcp.tool()
    def check_ai_features() -> str:
        """Report availability of Splunk AI Assistant, MLTK, and fallback modes."""
        return json.dumps(ai_assistant.check_ai_features(), indent=2)


def main():
    if mcp is None:
        print("Install MCP: pip install mcp")
        raise SystemExit(1)
    mcp.run()


if __name__ == "__main__":
    main()
