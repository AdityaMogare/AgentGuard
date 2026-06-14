#!/usr/bin/env python3
"""
Run all three infrastructure monitor agents and flush spans to Splunk HEC + Django.

Usage:
  export SPLUNK_HEC_URL=https://localhost:8088
  export SPLUNK_HEC_TOKEN=your-token
  python demo/run_demo.py --cycles 5

Backend-only (no Splunk):
  AGENTGUARD_BACKEND_URL=http://localhost:8001 python demo/run_demo.py --backend-only
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
sys.path.insert(0, str(ROOT / "demo"))

import agentguard
from agents.monitors import cpu_monitor_cycle, disk_monitor_cycle, memory_monitor_cycle


def main():
    parser = argparse.ArgumentParser(description="AgentGuard psutil demo")
    parser.add_argument("--cycles", type=int, default=3, help="Loops per agent")
    parser.add_argument(
        "--backend-only",
        action="store_true",
        help="Skip Splunk HEC; send only to Django backend",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds between cycles",
    )
    args = parser.parse_args()

    agentguard.configure(
        enable_splunk=not args.backend_only,
        enable_backend=True,
    )
    client = agentguard.get_client()
    print(f"AgentGuard exporters active: {client.exporter_count}")

    agents = [
        ("cpu_monitor", cpu_monitor_cycle),
        ("memory_monitor", memory_monitor_cycle),
        ("disk_monitor", disk_monitor_cycle),
    ]

    for cycle in range(args.cycles):
        print(f"\n--- Cycle {cycle + 1}/{args.cycles} ---")
        for name, fn in agents:
            try:
                result = fn()
                print(f"  {name}: {result}")
            except Exception as exc:
                print(f"  {name}: FAILED — {exc}")
        time.sleep(args.sleep)

    print("\nFlushing span queue...")
    client.stop_worker(timeout=5.0)
    print("Done. Check Splunk index=main sourcetype=agentguard:trace or GET /api/v1/agents/")


if __name__ == "__main__":
    main()
