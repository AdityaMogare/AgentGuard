"""Anomaly detection and failure-rate analysis with MLTK + built-in fallback."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import splunk_client

MOCK_TRACES = [
    {
        "agent_name": "cpu_monitor",
        "status": "FAILED",
        "latency_ms": "142.5",
        "error_type": "RuntimeError",
        "trace_id": "11111111-1111-1111-1111-111111111111",
        "anomaly_score": "0.91",
        "is_anomaly": "1",
    },
    {
        "agent_name": "memory_monitor",
        "status": "SUCCESS",
        "latency_ms": "18.4",
        "error_type": "",
        "trace_id": "22222222-2222-2222-2222-222222222222",
        "anomaly_score": "0.12",
        "is_anomaly": "0",
    },
    {
        "agent_name": "disk_monitor",
        "status": "FAILED",
        "latency_ms": "210.0",
        "error_type": "TimeoutError",
        "trace_id": "33333333-3333-3333-3333-333333333333",
        "anomaly_score": "0.88",
        "is_anomaly": "1",
    },
]


def mock_traces() -> List[Dict[str, Any]]:
    return list(MOCK_TRACES)


def builtin_anomaly_spl(agent_name: Optional[str] = None, minutes: int = 60) -> str:
    """Built-in anomalydetection (no MLTK required)."""
    base = splunk_client.trace_base_filter()
    agent_clause = f' AND agent_name="{agent_name}"' if agent_name else ""
    return (
        f"search {base}{agent_clause} earliest=-{minutes}m "
        f"| timechart span=5m count as event_count avg(latency_ms) as avg_latency by agent_name "
        f"| anomalydetection avg_latency "
        f"| where isOutlier(avg_latency)=1 OR isOutlier(event_count)=1 "
        f"| eval is_anomaly=1 "
        f"| table _time agent_name avg_latency event_count isOutlier*"
    )


def mltk_density_spl(agent_name: Optional[str] = None, minutes: int = 60) -> str:
    """MLTK DensityFunction on latency_ms (requires trained model or inline fit)."""
    base = splunk_client.trace_base_filter()
    agent_clause = f' agent_name="{agent_name}"' if agent_name else ""
    model = os.environ.get("AGENTGUARD_MLTK_MODEL", "agentguard_latency_density")
    return (
        f"search {base}{agent_clause} earliest=-{minutes}m "
        f"| fit DensityFunction latency_ms as anomaly_score into {model} "
        f"| apply {model} "
        f"| eval is_anomaly=if(anomaly_score>0.75, 1, 0) "
        f"| where is_anomaly=1 "
        f"| table _time agent_name latency_ms status anomaly_score is_anomaly trace_id"
    )


def failure_rate_spl(minutes: int = 60) -> str:
    base = splunk_client.trace_base_filter()
    return (
        f"search {base} earliest=-{minutes}m "
        "| stats count as total "
        'count(eval(status="FAILED" OR status="TIMEOUT")) as failures '
        'values(error_type) as error_types '
        "by agent_name "
        "| eval failure_rate=round(100*failures/total, 2) "
        "| sort -failure_rate"
    )


def pick_anomaly_spl(agent_name: Optional[str] = None, minutes: int = 60) -> Dict[str, str]:
    if splunk_client.use_mock():
        return {"engine": "mock", "spl": "mock://anomaly"}

    try:
        if splunk_client.check_mltk_installed():
            return {
                "engine": "mltk",
                "spl": mltk_density_spl(agent_name, minutes=max(minutes, 60)),
            }
    except Exception:
        pass

    return {"engine": "anomalydetection", "spl": builtin_anomaly_spl(agent_name, minutes)}


def anomaly_detection(
    agent_name: Optional[str] = None,
    minutes: int = 60,
) -> Dict[str, Any]:
    picked = pick_anomaly_spl(agent_name, minutes)
    if picked["engine"] == "mock":
        rows = mock_traces()
        anomalies = [r for r in rows if r.get("is_anomaly") == "1"]
        return {
            "engine": "mock",
            "spl": picked["spl"],
            "anomalies": anomalies,
            "anomaly_count": len(anomalies),
            "source": "mock",
        }

    try:
        rows, source = splunk_client.run_search(picked["spl"], count=100)
        return {
            "engine": picked["engine"],
            "spl": picked["spl"],
            "anomalies": rows,
            "anomaly_count": len(rows),
            "source": source,
        }
    except splunk_client.SearchError as exc:
        return {
            "engine": picked["engine"],
            "spl": picked["spl"],
            "error": str(exc),
            "anomalies": [],
            "anomaly_count": 0,
        }


def failure_rate_analysis(minutes: int = 60) -> Dict[str, Any]:
    spl = failure_rate_spl(minutes)
    if splunk_client.use_mock():
        agents = ["cpu_monitor", "memory_monitor", "disk_monitor"]
        rows = []
        for i, name in enumerate(agents):
            total = 50 - i * 10
            failures = 8 - i * 2
            rows.append(
                {
                    "agent_name": name,
                    "total": str(total),
                    "failures": str(failures),
                    "failure_rate": str(round(100 * failures / total, 2)),
                    "error_types": "RuntimeError" if failures else "",
                }
            )
        return {"spl": spl, "agents": rows, "source": "mock"}

    try:
        rows, source = splunk_client.run_search(spl, count=50)
        return {"spl": spl, "agents": rows, "source": source}
    except splunk_client.SearchError as exc:
        return {"spl": spl, "error": str(exc), "agents": [], "source": "error"}


def mock_alert_summary() -> List[Dict[str, Any]]:
    return [
        {
            "alert_name": "agentguard_failed_spans",
            "severity": "high",
            "count": "3",
            "latest_agent": "cpu_monitor",
        },
        {
            "alert_name": "agentguard_latency_anomaly",
            "severity": "medium",
            "count": "1",
            "latest_agent": "disk_monitor",
        },
    ]


def alert_summary_spl(hours: int = 24) -> str:
    return (
        f'index=_internal source=*scheduler* ("agentguard_failed" OR "agentguard_latency") '
        f"earliest=-{hours}h "
        "| rex field=_raw \"alert=\"(?<alert_name>[^\\\"]+)\" "
        "| stats count latest(_time) as last_fired by alert_name "
        "| sort -count"
    )


def alert_summary(hours: int = 24) -> Dict[str, Any]:
    if splunk_client.use_mock():
        return {
            "source": "mock",
            "alerts": mock_alert_summary(),
            "spl": alert_summary_spl(hours),
            "note": "Mock alert summary for demo without Splunk alert scheduler",
        }

    spl = alert_summary_spl(hours)
    try:
        rows, source = splunk_client.run_search(spl, count=25)
        return {"source": source, "alerts": rows, "spl": spl}
    except splunk_client.SearchError:
        # Fallback: summarize recent FAILED spans as proxy for fired alerts.
        spl = (
            f"search {splunk_client.trace_base_filter()} status=FAILED earliest=-{hours}h "
            "| stats count latest(_time) as last_seen by agent_name "
            "| eval alert_name=agent_name+\"_failures\", severity=\"high\" "
            "| rename count alert_count "
            "| fields alert_name severity alert_count last_seen agent_name"
        )
        try:
            rows, source = splunk_client.run_search(spl, count=25)
            return {
                "source": source,
                "alerts": rows,
                "spl": spl,
                "note": "Derived from FAILED spans (Splunk alert log unavailable)",
            }
        except splunk_client.SearchError as exc:
            return {"source": "error", "error": str(exc), "alerts": [], "spl": spl}
