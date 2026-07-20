"""Governed MCP server — the driver only ever sees ``step``."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    FastMCP = None  # type: ignore

from seer.fsm import InvestigationMachine, default_ledger_path
from seer.ledger import Ledger

mcp = FastMCP("agentguard-seer") if FastMCP else None

# In-process run registry (one active investigation per run_id)
_RUNS: Dict[str, InvestigationMachine] = {}


def _get_or_create(
    run_id: Optional[str] = None,
    persist: bool = True,
) -> InvestigationMachine:
    if run_id and run_id in _RUNS:
        return _RUNS[run_id]
    path = default_ledger_path(run_id) if (persist and run_id) else None
    if run_id:
        machine = InvestigationMachine(
            run_id=run_id,
            ledger=Ledger(run_id=run_id, path=path),
        )
    else:
        machine = InvestigationMachine()
        if persist:
            machine.ledger.path = default_ledger_path(machine.run_id)
    _RUNS[machine.run_id] = machine
    return machine


if mcp:

    @mcp.tool()
    def step(
        action: str,
        inputs_json: str = "{}",
        run_id: Optional[str] = None,
    ) -> str:
        """
        Advance the AgentGuard seer investigation state machine.

        Legal actions depend on current state (illegal moves are refused and
        recorded). Typical path:
          detect → localize → correlate → analyze → audit → remediate → publish → finish

        inputs_json: JSON object of action inputs (e.g. {"minutes": 60, "agent_name": "cpu_monitor"}).
        run_id: continue an existing investigation; omit to start a new one.
        """
        try:
            inputs: Dict[str, Any] = json.loads(inputs_json or "{}")
            if not isinstance(inputs, dict):
                raise ValueError("inputs_json must be a JSON object")
        except json.JSONDecodeError as exc:
            return json.dumps({"ok": False, "error": f"invalid inputs_json: {exc}"})

        machine = _get_or_create(run_id)
        result = machine.step(action, inputs)
        payload = result.to_dict()
        payload["run_id"] = machine.run_id
        payload["ledger_head"] = machine.ledger.head_hash
        return json.dumps(payload, indent=2, default=str)

    @mcp.tool()
    def status(run_id: str) -> str:
        """Return current state, legal next actions, and verdict for a run."""
        machine = _RUNS.get(run_id)
        if not machine:
            return json.dumps({"error": f"unknown run_id: {run_id}"})
        return json.dumps(
            {
                "run_id": run_id,
                "state": machine.state.value,
                "next_actions": machine.legal_actions(),
                "verdict": machine.context.get("verdict"),
                "focus_agent": machine.context.get("focus_agent"),
                "ledger_head": machine.ledger.head_hash,
                "ledger_entries": len(machine.ledger.entries),
            },
            indent=2,
            default=str,
        )

    @mcp.tool()
    def run_investigation(
        agent_name: Optional[str] = None,
        minutes: int = 60,
        remediate: bool = True,
        publish_hec: bool = True,
    ) -> str:
        """
        Autonomously walk the full legal path: detect → … → finish.
        Prefer ``step`` when a driving agent should decide each move.
        """
        machine = _get_or_create()
        result = machine.run_to_completion(
            agent_name=agent_name,
            minutes=minutes,
            remediate=remediate,
            publish_hec=publish_hec,
        )
        return json.dumps(result, indent=2, default=str)


def main() -> None:
    if mcp is None:
        print("Install MCP: pip install mcp")
        raise SystemExit(1)
    mcp.run()


if __name__ == "__main__":
    main()
