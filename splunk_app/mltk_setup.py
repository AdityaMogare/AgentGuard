#!/usr/bin/env python3
"""
MLTK setup helper for AgentGuard Splunk app.

Checks whether Splunk ML Toolkit is installed and prints the SPL to train
latency/failure anomaly models. Falls back to built-in anomalydetection when
MLTK is unavailable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server import splunk_client  # noqa: E402
from mcp_server.anomaly import (  # noqa: E402
    builtin_anomaly_spl,
    mltk_density_spl,
    failure_rate_spl,
)


def check_mltk() -> dict:
    mock = splunk_client.use_mock()
    if mock:
        return {
            "mltk_installed": False,
            "mode": "mock",
            "message": "SPLUNK_MOCK=1 — skipping live Splunk checks",
        }
    try:
        installed = splunk_client.check_mltk_installed()
        return {
            "mltk_installed": installed,
            "mode": "mltk" if installed else "anomalydetection",
            "message": (
                "MLTK detected — use DensityFunction model SPL"
                if installed
                else "MLTK not found — use built-in anomalydetection SPL"
            ),
        }
    except Exception as exc:
        return {
            "mltk_installed": False,
            "mode": "anomalydetection",
            "message": f"Check failed ({exc}) — using anomalydetection fallback",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentGuard MLTK setup checker")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--minutes", type=int, default=60)
    args = parser.parse_args()

    status = check_mltk()
    model = os.environ.get("AGENTGUARD_MLTK_MODEL", "agentguard_latency_density")
    spl_mltk = mltk_density_spl(minutes=args.minutes)
    spl_builtin = builtin_anomaly_spl(minutes=args.minutes)
    spl_failures = failure_rate_spl(minutes=args.minutes)

    output = {
        **status,
        "model_name": model,
        "recommended_spl": spl_mltk if status.get("mltk_installed") else spl_builtin,
        "mltk_train_spl": spl_mltk,
        "builtin_anomaly_spl": spl_builtin,
        "failure_rate_spl": spl_failures,
        "saved_search": "agentguard_latency_anomaly",
    }

    if args.json:
        print(json.dumps(output, indent=2))
        return

    print("=== AgentGuard MLTK Setup ===")
    print(f"Mode: {output['mode']}")
    print(output["message"])
    print()
    print("Recommended anomaly SPL:")
    print(output["recommended_spl"])
    print()
    print("Failure rate SPL:")
    print(spl_failures)
    print()
    print("Install MLTK: Splunkbase app Splunk_ML_Toolkit")


if __name__ == "__main__":
    main()
