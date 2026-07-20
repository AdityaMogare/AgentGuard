"""Tests for AgentGuard seer — governed investigation loop."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force mock Splunk for unit tests
os.environ["SPLUNK_MOCK"] = "1"

from seer.correlate import classify_verdict, correlate_window
from seer.fsm import InvestigationMachine, InvestigationState
from seer.ledger import Ledger, hash_entry, verify_ledger
from seer.remediate import apply_edits


class LedgerTests(unittest.TestCase):
    def test_hash_chain_roundtrip(self):
        led = Ledger(run_id="test-run")
        led.append(
            kind="step",
            action="detect",
            state_before="START",
            state_after="DETECTED",
            inputs={"minutes": 60},
            outputs={"ok": True},
            ok=True,
            detail="ok",
        )
        led.append(
            kind="refuse",
            action="publish",
            state_before="DETECTED",
            state_after="DETECTED",
            inputs={},
            outputs={"valid_next": ["localize", "clear"]},
            ok=False,
            detail="illegal",
        )
        ok, errors = verify_ledger(led)
        self.assertTrue(ok, errors)
        self.assertEqual(len(led.entries), 2)
        self.assertNotEqual(led.entries[0].hash, led.entries[1].hash)

    def test_tamper_detected(self):
        led = Ledger(run_id="tamper")
        led.append(
            kind="step",
            action="detect",
            state_before="START",
            state_after="DETECTED",
            inputs={},
            outputs={},
            ok=True,
            detail="x",
        )
        rows = led.dump()
        rows[0]["detail"] = "tampered"
        ok, errors = verify_ledger(rows)
        self.assertFalse(ok)
        self.assertTrue(any("hash mismatch" in e for e in errors))

    def test_persist_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            led = Ledger(run_id="persist-me", path=path)
            led.append(
                kind="step",
                action="detect",
                state_before="START",
                state_after="DETECTED",
                inputs={},
                outputs={"a": 1},
                ok=True,
                detail="d",
            )
            reloaded = Ledger(path=path)
            self.assertEqual(reloaded.run_id, "persist-me")
            ok, errors = verify_ledger(reloaded)
            self.assertTrue(ok, errors)


class FsmTests(unittest.TestCase):
    def test_refuse_illegal_step(self):
        m = InvestigationMachine()
        r = m.step("publish", {})
        self.assertTrue(r.refused)
        self.assertEqual(m.state, InvestigationState.START)
        ok, _ = verify_ledger(m.ledger)
        self.assertTrue(ok)
        self.assertEqual(m.ledger.entries[0].kind, "refuse")

    def test_legal_detect(self):
        m = InvestigationMachine()
        r = m.step("detect", {"minutes": 60})
        self.assertTrue(r.ok)
        self.assertFalse(r.refused)
        self.assertIn(m.state, (InvestigationState.DETECTED, InvestigationState.DONE))

    def test_full_loop_mock(self):
        m = InvestigationMachine()
        result = m.run_to_completion(
            minutes=60,
            remediate=True,
            publish_hec=False,
        )
        self.assertEqual(result["state"], "DONE")
        ok, errors = verify_ledger(m.ledger)
        self.assertTrue(ok, errors)
        verdict = result.get("verdict") or {}
        self.assertIn(verdict.get("status"), ("REGRESSION", "DEGRADING", "CLEAR", "UNKNOWN"))


class CorrelateTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(
            classify_verdict(
                failure_rate=20, error_count=5, anomaly_count=0, p95_latency=50
            ),
            "REGRESSION",
        )
        self.assertEqual(
            classify_verdict(
                failure_rate=0, error_count=0, anomaly_count=2, p95_latency=200
            ),
            "DEGRADING",
        )
        self.assertEqual(
            classify_verdict(
                failure_rate=0, error_count=0, anomaly_count=0, p95_latency=10
            ),
            "CLEAR",
        )

    def test_window_correlation_mock(self):
        out = correlate_window(
            agent_name="cpu_monitor",
            earliest_epoch=1_700_000_000,
            latest_epoch=1_700_000_060,
        )
        self.assertEqual(out["verdict_hint"], "REGRESSION")
        self.assertGreater(out["error_count"], 0)
        self.assertIn("spl", out)


class RemediateTests(unittest.TestCase):
    def test_apply_parse_gate_and_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "demo" / "agents" / "cpu_monitor.py"
            target.parent.mkdir(parents=True)
            target.write_text(
                "import time\n\ntimeout = 5\n\ndef run():\n    return 1\n",
                encoding="utf-8",
            )
            edits = [
                {
                    "path": "demo/agents/cpu_monitor.py",
                    "action": "replace_regex",
                    "pattern": r"(timeout\s*=\s*)\d+(\.\d+)?",
                    "replacement": r"\g<1>30.0",
                    "rationale": "raise timeout",
                }
            ]
            result = apply_edits(edits, dry_run=True, root=root)
            self.assertTrue(result["ok"], result)
            self.assertIn("30.0", result["unified_diff"])
            # Original untouched in dry_run
            self.assertIn("timeout = 5", target.read_text(encoding="utf-8"))

    def test_parse_gate_rejects_syntax_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "broken.py"
            target.write_text("def ok():\n    return 1\n", encoding="utf-8")
            edits = [
                {
                    "path": "broken.py",
                    "action": "replace_span",
                    "old_str": "return 1",
                    "new_str": "return (",
                }
            ]
            result = apply_edits(edits, dry_run=True, root=root)
            self.assertFalse(result["ok"])
            self.assertIn("parse gate", result["error"])


class VerifyCliTests(unittest.TestCase):
    def test_verify_module(self):
        from seer.verify import main

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "led.jsonl"
            led = Ledger(run_id="cli", path=path)
            led.append(
                kind="step",
                action="detect",
                state_before="START",
                state_after="DETECTED",
                inputs={},
                outputs={},
                ok=True,
                detail="d",
            )
            self.assertEqual(main([str(path), "--quiet"]), 0)


if __name__ == "__main__":
    unittest.main()
