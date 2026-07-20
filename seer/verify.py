#!/usr/bin/env python3
"""Verify an AgentGuard seer hash-chained ledger.

Usage:
  python -m seer.verify path/to/run.jsonl
  agentguard-verify path/to/run.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seer.ledger import load_ledger_file, verify_ledger


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Verify AgentGuard seer ledger integrity")
    p.add_argument("ledger", help="Path to ledger JSONL")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    rows = load_ledger_file(args.ledger)
    ok, errors = verify_ledger(rows)
    if not args.quiet:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "entries": len(rows),
                    "errors": errors,
                    "head": rows[-1]["hash"] if rows else None,
                    "run_id": rows[0]["run_id"] if rows else None,
                },
                indent=2,
            )
        )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
