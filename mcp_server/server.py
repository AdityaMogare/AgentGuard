#!/usr/bin/env python3
"""
AgentGuard Splunk MCP server — 3 tools for querying agent telemetry.

Environment:
  SPLUNK_MOCK=1          Return sample data (default for local dev)
  SPLUNK_MOCK=0          Use Splunk REST; falls back to Django API on failure
  SPLUNK_HOST            https://localhost:8089
  SPLUNK_REST_TOKEN      Splunk management token
  AGENTGUARD_BACKEND_URL http://localhost:8000 (Django fallback)
"""
from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    FastMCP = None  # type: ignore

mcp = FastMCP("agentguard-splunk") if FastMCP else None

MOCK_TRACES = [
    {
        "trace_id": "11111111-1111-1111-1111-111111111111",
        "agent_name": "cpu_monitor",
        "status": "FAILED",
        "error_type": "RuntimeError",
        "latency_ms": 42.1,
        "tool_name": "psutil.cpu_percent",
    },
    {
        "trace_id": "22222222-2222-2222-2222-222222222222",
        "agent_name": "memory_monitor",
        "status": "SUCCESS",
        "error_type": None,
        "latency_ms": 18.4,
        "tool_name": None,
    },
]


class SearchError(Exception):
    """Raised when Splunk and Django fallback both fail."""


def _use_mock() -> bool:
    return os.environ.get("SPLUNK_MOCK", "1").strip() == "1"


def _splunk_session():
    import requests

    host = os.environ.get("SPLUNK_HOST", "https://localhost:8089").rstrip("/")
    token = os.environ.get("SPLUNK_REST_TOKEN", "").strip()
    verify = os.environ.get("SPLUNK_VERIFY_SSL", "0").strip() == "1"
    if not token:
        raise SearchError("SPLUNK_REST_TOKEN not set")
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    session.verify = verify
    return session, host


def _splunk_create_job(session, host: str, spl: str) -> str:
    resp = session.post(
        f"{host}/services/search/jobs",
        data={"search": spl, "exec_mode": "normal"},
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    sid = root.findtext(".//sid")
    if not sid:
        raise SearchError(f"Splunk job creation failed: no sid in response")
    return sid


def _splunk_wait_job(session, host: str, sid: str, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = session.get(
            f"{host}/services/search/jobs/{sid}",
            params={"output_mode": "json"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        entry = (payload.get("entry") or [{}])[0]
        content = entry.get("content", {})
        if content.get("isDone") in (True, "1", 1):
            if int(content.get("dispatchState", 0)) == -1 or content.get("isFailed"):
                raise SearchError(f"Splunk job {sid} failed")
            return
        time.sleep(0.5)
    raise SearchError(f"Splunk job {sid} timed out after {timeout}s")


def _splunk_fetch_results(session, host: str, sid: str, count: int = 50) -> List[Dict[str, Any]]:
    resp = session.get(
        f"{host}/services/search/jobs/{sid}/results",
        params={"output_mode": "json", "count": count},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("results") or []


def _splunk_search(spl: str, count: int = 50) -> Tuple[List[Dict[str, Any]], str]:
    """Run SPL via Splunk REST job API. Returns (rows, source)."""
    session, host = _splunk_session()
    sid = _splunk_create_job(session, host, spl)
    _splunk_wait_job(session, host, sid)
    rows = _splunk_fetch_results(session, host, sid, count=count)
    return rows, "splunk"


def _django_backend_url() -> str:
    return os.environ.get("AGENTGUARD_BACKEND_URL", "http://localhost:8000").rstrip("/")


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
    """
    Execute search with mock / Splunk / Django fallback chain.
    """
    if _use_mock():
        return {"source": "mock", "results": MOCK_TRACES, "spl": spl, "warning": None}

    errors: List[str] = []
    try:
        rows, source = _splunk_search(spl, count=count)
        return {"source": source, "results": rows, "spl": spl, "warning": None}
    except Exception as exc:
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

    raise SearchError("; ".join(errors))


if mcp:

    @mcp.tool()
    def search_agent_traces(
        agent_name: Optional[str] = None,
        status: Optional[str] = None,
        minutes: int = 15,
    ) -> str:
        """Search recent agent spans in Splunk. Filter by agent_name and status (SUCCESS/FAILED/TIMEOUT)."""
        clauses = ['sourcetype="agentguard:trace"']
        if agent_name:
            clauses.append(f'agent_name="{agent_name}"')
        if status:
            clauses.append(f'status="{status}"')
        spl = f"search index=main {' '.join(clauses)} earliest=-{minutes}m | head 50"

        def django_fb():
            return _django_list_agents(agent_name=agent_name, status=status)

        try:
            payload = query_rows(spl, django_fallback=django_fb)
            return json.dumps(payload, indent=2)
        except SearchError as exc:
            return json.dumps({"error": str(exc), "spl": spl}, indent=2)

    @mcp.tool()
    def explain_agent_failure(trace_id: str) -> str:
        """Return span details and error context for a failed agent trace_id."""
        spl = (
            f'search index=main sourcetype="agentguard:trace" trace_id="{trace_id}" '
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
        except SearchError as exc:
            return json.dumps({"error": str(exc), "trace_id": trace_id, "spl": spl}, indent=2)

    @mcp.tool()
    def agent_health_summary(minutes: int = 60) -> str:
        """Aggregate pass/fail rates and top error types by agent_name."""
        spl = (
            f"search index=main sourcetype=agentguard:trace earliest=-{minutes}m "
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
        except SearchError as exc:
            return json.dumps({"error": str(exc), "spl": spl}, indent=2)


def main():
    if mcp is None:
        print("Install MCP: pip install mcp")
        raise SystemExit(1)
    mcp.run()


if __name__ == "__main__":
    main()
