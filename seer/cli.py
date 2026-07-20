"""CLI: investigate, verify ledgers, run the seer MCP server."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seer.fsm import InvestigationMachine, default_ledger_path
from seer.ledger import Ledger, load_ledger_file, verify_ledger


def cmd_investigate(args: argparse.Namespace) -> int:
    path = Path(args.ledger) if args.ledger else None
    machine = InvestigationMachine(
        ledger=Ledger(path=path) if path else Ledger(),
    )
    if path is None and args.persist:
        machine.ledger.path = default_ledger_path(machine.run_id)

    result = machine.run_to_completion(
        agent_name=args.agent,
        minutes=args.minutes,
        remediate=not args.no_remediate,
        remediations=None,
        publish_hec=not args.no_hec,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("state") == "DONE" else 1


def cmd_verify(args: argparse.Namespace) -> int:
    rows = load_ledger_file(args.ledger)
    ok, errors = verify_ledger(rows)
    out = {
        "ok": ok,
        "entries": len(rows),
        "errors": errors,
        "head": rows[-1]["hash"] if rows else None,
        "run_id": rows[0]["run_id"] if rows else None,
    }
    print(json.dumps(out, indent=2))
    return 0 if ok else 2


def cmd_step(args: argparse.Namespace) -> int:
    path = Path(args.ledger) if args.ledger else default_ledger_path(args.run_id or "adhoc")
    ledger = Ledger(run_id=args.run_id or "adhoc", path=path if path.exists() or args.persist else None)
    if ledger.path is None and path.exists():
        ledger = Ledger(path=path)
    machine = InvestigationMachine(run_id=ledger.run_id, ledger=ledger)
    # Restore state from last entry if resuming
    if ledger.entries:
        last = ledger.entries[-1]
        from seer.fsm import InvestigationState

        try:
            machine.state = InvestigationState(last.state_after)
        except ValueError:
            pass
    inputs = json.loads(args.inputs or "{}")
    result = machine.step(args.action, inputs)
    print(json.dumps({"run_id": machine.run_id, **result.to_dict()}, indent=2, default=str))
    return 0 if result.ok or result.refused else 1


def cmd_mcp(_args: argparse.Namespace) -> int:
    from seer.mcp_step import main as mcp_main

    mcp_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentguard-seer",
        description="Governed agent-native investigation (detect → fix → seal).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("investigate", help="Run full investigation loop")
    inv.add_argument("--agent", default=None, help="Focus agent_name")
    inv.add_argument("--minutes", type=int, default=60)
    inv.add_argument("--ledger", default=None, help="Ledger JSONL path")
    inv.add_argument("--persist", action="store_true", help="Write ledger under .agentguard/ledgers")
    inv.add_argument("--no-remediate", action="store_true")
    inv.add_argument("--no-hec", action="store_true", help="Skip Splunk HEC publish")
    inv.set_defaults(func=cmd_investigate)

    ver = sub.add_parser("verify", help="Verify a hash-chained ledger")
    ver.add_argument("ledger", help="Path to ledger JSONL")
    ver.set_defaults(func=cmd_verify)

    st = sub.add_parser("step", help="Take one governed step")
    st.add_argument("action")
    st.add_argument("--inputs", default="{}", help="JSON object")
    st.add_argument("--run-id", default=None)
    st.add_argument("--ledger", default=None)
    st.add_argument("--persist", action="store_true")
    st.set_defaults(func=cmd_step)

    mcp = sub.add_parser("mcp", help="Run seer MCP server (single step tool)")
    mcp.set_defaults(func=cmd_mcp)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
