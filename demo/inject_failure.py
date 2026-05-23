#!/usr/bin/env python3
"""
Inject a failed agent span for demo / judge walkthrough.
Shows FAILED status + error_type in Splunk and Django.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

import agentguard
from agentguard import trace_agent, trace_tool


@trace_tool(tool_name="psutil.cpu_percent")
def flaky_cpu_read():
    raise RuntimeError("Simulated sensor read failure — CPU agent offline")


@trace_agent(agent_name="cpu_monitor", action_type="error")
def failing_cpu_cycle():
    return flaky_cpu_read()


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-only", action="store_true")
    args = parser.parse_args()

    agentguard.configure(
        enable_splunk=not args.backend_only,
        enable_backend=True,
    )

    print("Injecting failure into cpu_monitor...")
    try:
        failing_cpu_cycle()
    except RuntimeError as exc:
        print(f"Expected failure: {exc}")

    client = agentguard.get_client()
    time.sleep(1.0)
    client.stop_worker(timeout=5.0)
    print(
        "Failure span sent. In Splunk: "
        'index=main sourcetype=agentguard:trace status=FAILED agent_name=cpu_monitor'
    )


if __name__ == "__main__":
    main()
