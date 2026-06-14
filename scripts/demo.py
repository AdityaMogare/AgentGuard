#!/usr/bin/env python3
"""
AgentGuard hackathon demo — inject traces, run AI/ML MCP features, print summary.

Works without Splunk (SPLUNK_MOCK=1 + Django backend). With Splunk configured,
also sends spans via HEC.

Usage:
  python scripts/demo.py
  python scripts/demo.py --traces 150 --backend-only
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sdk"))

import requests

import agentguard
from mcp_server import ai_assistant, anomaly

AGENTS = ["cpu_monitor", "memory_monitor", "disk_monitor", "router_agent", "research_agent"]
TOOLS = ["psutil.cpu_percent", "search_kb", "openai.chat.completions"]
ERRORS = ["RuntimeError", "TimeoutError", "ConnectionError"]


def _backend_url() -> str:
    return os.environ.get("AGENTGUARD_BACKEND_URL", "http://localhost:8001").rstrip("/")


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("AGENTGUARD_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Api-Key {key}"
    return headers


def _span_payload(i: int) -> dict:
    agent = AGENTS[i % len(AGENTS)]
    fail = i % 17 == 0 or random.random() < 0.1
    status = "FAILED" if fail else "SUCCESS"
    latency = random.uniform(8, 180) if fail else random.uniform(5, 45)
    return {
        "span": {
            "trace_id": str(uuid.uuid4()),
            "span_id": str(uuid.uuid4()),
            "parent_span_id": None,
            "agent_name": agent,
            "action_type": random.choice(["observe", "act", "tool"]),
            "tool_name": random.choice(TOOLS),
            "status": status,
            "error_type": random.choice(ERRORS) if fail else None,
            "latency_ms": round(latency, 2),
            "input": {"cycle": i, "threshold": 90},
            "output": "ok" if not fail else "error",
            "prompt_tokens": random.randint(0, 400),
            "completion_tokens": random.randint(0, 200),
            "cost": round(random.uniform(0, 0.002), 6),
        }
    }


def inject_traces_api(count: int) -> int:
    """Fast inject via Django ingest API."""
    url = f"{_backend_url()}/api/v1/spans/ingest/"
    ok = 0
    for i in range(count):
        try:
            resp = requests.post(url, json=_span_payload(i), headers=_headers(), timeout=5)
            if resp.status_code in (200, 201):
                ok += 1
        except requests.RequestException:
            pass
        if (i + 1) % 25 == 0:
            print(f"  … {i + 1}/{count} spans sent ({ok} ok)")
    return ok


def inject_traces_sdk(count: int, backend_only: bool) -> None:
    """Inject via SDK exporters (Splunk HEC + backend)."""
    agentguard.configure(enable_splunk=not backend_only, enable_backend=True)
    client = agentguard.get_client()
    print(f"  SDK exporters active: {client.exporter_count}")
    url = f"{_backend_url()}/api/v1/spans/ingest/"
    for i in range(count):
        payload = _span_payload(i)
        event = payload["span"]
        event["timestamp"] = time.time()
        client.log_span(event)
        if (i + 1) % 25 == 0:
            print(f"  … {i + 1}/{count} spans queued")
    time.sleep(2.0)
    client.stop_worker(timeout=15.0)


def inject_traces(count: int, backend_only: bool) -> None:
    try:
        resp = requests.get(f"{_backend_url()}/api/v1/agents/", timeout=3)
        backend_up = resp.status_code == 200
    except requests.RequestException:
        backend_up = False

    if backend_up and backend_only:
        ok = inject_traces_api(count)
        print(f"  Injected {ok}/{count} spans via Django API")
        return

    if backend_up:
        inject_traces_sdk(count, backend_only=False)
        return

    print("  Django not running — using SDK queue only (start: manage.py runserver)")
    inject_traces_sdk(count, backend_only=backend_only)


def run_mcp_features() -> dict:
    print("\n[2/4] MCP AI/ML features")
    features = ai_assistant.check_ai_features()
    print(f"  check_ai_features: mock={features.get('splunk_mock')} mltk={features.get('mltk_installed')}")

    nl = ai_assistant.nl_search("show failed agents in the last hour")
    print(f"  nl_search: method={nl.get('method')} results={nl.get('result_count', 0)}")
    print(f"    SPL: {nl.get('spl', '')[:100]}…")

    anom = anomaly.anomaly_detection(minutes=60)
    print(f"  anomaly_detection: engine={anom.get('engine')} count={anom.get('anomaly_count', 0)}")

    fail = anomaly.failure_rate_analysis(minutes=60)
    print(f"  failure_rate_analysis: agents={len(fail.get('agents') or [])} source={fail.get('source')}")

    alerts = anomaly.alert_summary(hours=24)
    print(f"  alert_summary: items={len(alerts.get('alerts') or [])} source={alerts.get('source')}")

    return {"features": features, "nl": nl, "anomaly": anom, "failure_rate": fail, "alerts": alerts}


def simulate_webhook() -> None:
    print("\n[3/4] Simulating Splunk alert webhook")
    url = f"{_backend_url()}/api/v1/alerts/webhook/"
    payload = {
        "alert_name": "agentguard_failed_spans_alert",
        "severity": "high",
        "agent_name": "cpu_monitor",
        "trace_id": str(uuid.uuid4()),
        "message": "Demo alert: failed spans detected in 5m window",
    }
    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=10)
        print(f"  Webhook POST {resp.status_code}: {resp.json()}")
    except Exception as exc:
        print(f"  Webhook skipped: {exc}")


def print_summary(trace_count: int, mcp_result: dict) -> None:
    print("\n[4/4] Demo summary (screen-record this)")
    print("=" * 60)
    print("AgentGuard Hackathon Demo — READY")
    print("=" * 60)
    print(f"  Traces injected:     {trace_count}")
    print(f"  NL→SPL method:       {mcp_result['nl'].get('method')}")
    print(f"  Anomaly engine:      {mcp_result['anomaly'].get('engine')}")
    print(f"  Failure agents:      {len(mcp_result['failure_rate'].get('agents') or [])}")
    print(f"  MLTK installed:      {mcp_result['features'].get('mltk_installed')}")
    print(f"  Splunk mock mode:    {mcp_result['features'].get('splunk_mock')}")
    print()
    print("Claude MCP tools (8 total):")
    print("  check_ai_features | nl_search | anomaly_detection")
    print("  failure_rate_analysis | alert_summary")
    print("  search_agent_traces | explain_agent_failure | agent_health_summary")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="AgentGuard end-to-end hackathon demo")
    parser.add_argument("--traces", type=int, default=150)
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--skip-inject", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("SPLUNK_MOCK", "1")

    print("AgentGuard Demo Pipeline")
    print(f"  SPLUNK_MOCK={os.environ.get('SPLUNK_MOCK')}")
    print(f"  Backend={_backend_url()}")

    if not args.skip_inject:
        print(f"\n[1/4] Injecting {args.traces} agent traces…")
        inject_traces(args.traces, backend_only=args.backend_only)
    else:
        print("\n[1/4] Skipping trace injection")

    mcp_result = run_mcp_features()
    simulate_webhook()
    print_summary(args.traces if not args.skip_inject else 0, mcp_result)


if __name__ == "__main__":
    main()
