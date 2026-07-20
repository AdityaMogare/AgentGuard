"""Deterministic client/server-style correlation over an exact wall-clock window.

SPL is composed in Python — the model never writes queries. Window bounds are
epoch seconds so near-instant runs still produce a non-zero search span.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server import anomaly, splunk_client  # noqa: E402

from . import actions


def _epoch_bounds(earliest_epoch: float, latest_epoch: float) -> tuple[float, float]:
    """Ensure a minimum 1s window so Splunk returns rows for short runs."""
    if latest_epoch < earliest_epoch:
        earliest_epoch, latest_epoch = latest_epoch, earliest_epoch
    if latest_epoch - earliest_epoch < 1.0:
        latest_epoch = earliest_epoch + 1.0
    return earliest_epoch, latest_epoch


def window_filter(earliest_epoch: float, latest_epoch: float) -> str:
    earliest_epoch, latest_epoch = _epoch_bounds(earliest_epoch, latest_epoch)
    # Splunk absolute time: earliest/latest as epoch
    return f"earliest={int(earliest_epoch)} latest={int(latest_epoch) + 1}"


def correlation_spl(
    agent_name: Optional[str],
    earliest_epoch: float,
    latest_epoch: float,
) -> str:
    base = splunk_client.trace_base_filter()
    agent_clause = f' agent_name="{agent_name}"' if agent_name else ""
    win = window_filter(earliest_epoch, latest_epoch)
    return (
        f"search {base}{agent_clause} {win} "
        "| stats count as total "
        'count(eval(status="SUCCESS")) as success '
        'count(eval(status="FAILED" OR status="TIMEOUT")) as failures '
        "avg(latency_ms) as avg_latency "
        "p95(latency_ms) as p95_latency "
        "values(error_type) as error_types "
        "values(tool_name) as tools "
        "by agent_name "
        "| eval failure_rate=if(total>0, round(100*failures/total, 2), 0)"
    )


def error_rollup_spl(
    agent_name: Optional[str],
    earliest_epoch: float,
    latest_epoch: float,
) -> str:
    base = splunk_client.trace_base_filter()
    agent_clause = f' agent_name="{agent_name}"' if agent_name else ""
    win = window_filter(earliest_epoch, latest_epoch)
    return (
        f"search {base}{agent_clause} (status=FAILED OR status=TIMEOUT) {win} "
        "| stats count as error_count values(trace_id) as trace_ids "
        "by agent_name error_type tool_name action_type "
        "| sort -error_count"
    )


def _mock_correlation(agent_name: Optional[str]) -> Dict[str, Any]:
    """Offline ground-truth-ish rollup from anomaly mock traces."""
    rows = anomaly.mock_traces()
    if agent_name:
        rows = [r for r in rows if r.get("agent_name") == agent_name]
    total = len(rows) or 1
    failures = [r for r in rows if r.get("status") in ("FAILED", "TIMEOUT")]
    latencies = [float(r.get("latency_ms") or 0) for r in rows]
    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
    p95 = sorted(latencies)[max(0, int(0.95 * len(latencies)) - 1)] if latencies else 0.0
    error_types = sorted({r.get("error_type") for r in failures if r.get("error_type")})
    agents = sorted({r.get("agent_name") for r in rows if r.get("agent_name")})
    rollup = [
        {
            "agent_name": a or agent_name or "unknown",
            "total": str(sum(1 for r in rows if r.get("agent_name") == a)),
            "failures": str(sum(1 for r in failures if r.get("agent_name") == a)),
            "avg_latency": str(avg_lat),
            "p95_latency": str(p95),
            "error_types": ",".join(error_types),
            "failure_rate": str(
                round(
                    100
                    * sum(1 for r in failures if r.get("agent_name") == a)
                    / max(1, sum(1 for r in rows if r.get("agent_name") == a)),
                    2,
                )
            ),
        }
        for a in (agents or [agent_name or "unknown"])
    ]
    error_rows = [
        {
            "agent_name": r.get("agent_name"),
            "error_type": r.get("error_type"),
            "tool_name": r.get("tool_name") or "",
            "action_type": "error",
            "error_count": "1",
            "trace_ids": r.get("trace_id"),
        }
        for r in failures
    ]
    anomalies = [r for r in rows if r.get("is_anomaly") == "1"]
    return {
        "rollup": rollup,
        "errors": error_rows,
        "anomalies": anomalies,
        "source": "mock",
    }


def classify_verdict(
    *,
    failure_rate: float,
    error_count: int,
    anomaly_count: int,
    p95_latency: float,
    baseline_p95: float = 100.0,
) -> str:
    """Deterministic verdict hint from correlation numbers (not the narrative)."""
    if error_count > 0 and failure_rate >= 5.0:
        return "REGRESSION"
    if anomaly_count > 0 and p95_latency >= baseline_p95 * 1.5:
        return "DEGRADING"
    if anomaly_count > 0:
        return "DEGRADING"
    if failure_rate < 5.0 and error_count == 0:
        return "CLEAR"
    return "UNKNOWN"


def correlate_window(
    *,
    agent_name: Optional[str],
    earliest_epoch: float,
    latest_epoch: float,
) -> Dict[str, Any]:
    earliest_epoch, latest_epoch = _epoch_bounds(earliest_epoch, latest_epoch)
    spl = correlation_spl(agent_name, earliest_epoch, latest_epoch)
    err_spl = error_rollup_spl(agent_name, earliest_epoch, latest_epoch)

    if splunk_client.use_mock():
        mock = _mock_correlation(agent_name)
        rollup = mock["rollup"]
        errors = mock["errors"]
        anomalies = mock["anomalies"]
        source = "mock"
        engine = "mock"
    else:
        try:
            rollup, source = splunk_client.run_search(spl, count=50)
        except splunk_client.SearchError as exc:
            return {
                "error": str(exc),
                "spl": spl,
                "verdict_hint": "UNKNOWN",
                "window": {"earliest": earliest_epoch, "latest": latest_epoch},
            }
        try:
            errors, _ = splunk_client.run_search(err_spl, count=50)
        except splunk_client.SearchError:
            errors = []
        anom = actions.run_anomaly(agent_name=agent_name, minutes=max(1, int((latest_epoch - earliest_epoch) / 60) or 60))
        anomalies = anom.get("anomalies") or []
        engine = anom.get("engine")

    def _f(row: Dict[str, Any], key: str, default: float = 0.0) -> float:
        try:
            return float(row.get(key) or default)
        except (TypeError, ValueError):
            return default

    failure_rate = max((_f(r, "failure_rate") for r in rollup), default=0.0)
    p95 = max((_f(r, "p95_latency") for r in rollup), default=0.0)
    error_count = 0
    for r in errors:
        try:
            error_count += int(float(r.get("error_count") or 0))
        except (TypeError, ValueError):
            error_count += 1

    anomaly_count = len(anomalies)
    hint = classify_verdict(
        failure_rate=failure_rate,
        error_count=error_count,
        anomaly_count=anomaly_count,
        p95_latency=p95,
    )

    top_error = None
    if errors:
        top_error = {
            "error_type": errors[0].get("error_type"),
            "tool_name": errors[0].get("tool_name"),
            "agent_name": errors[0].get("agent_name"),
            "count": errors[0].get("error_count"),
        }

    return {
        "window": {"earliest": earliest_epoch, "latest": latest_epoch},
        "spl": spl,
        "error_spl": err_spl,
        "source": source if not splunk_client.use_mock() else "mock",
        "anomaly_engine": engine,
        "rollup": rollup,
        "errors": errors,
        "anomalies": anomalies,
        "failure_rate": failure_rate,
        "p95_latency": p95,
        "error_count": error_count,
        "anomaly_count": anomaly_count,
        "top_error": top_error,
        "verdict_hint": hint,
        "agent_name": agent_name,
    }
