"""Publish investigation walk + sealed verdict back to Splunk HEC."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from .ledger import Ledger

logger = logging.getLogger("agentguard.seer")


def publish_run(
    *,
    run_id: str,
    ledger: Ledger,
    verdict: Dict[str, Any],
    context: Dict[str, Any],
    hec: bool = True,
) -> Dict[str, Any]:
    """Emit one HEC event per ledger step + a final verdict event."""
    events = []
    for entry in ledger.entries:
        events.append(
            {
                "event_type": "seer_step",
                "run_id": run_id,
                "kind": entry.kind,
                "action": entry.action,
                "state_before": entry.state_before,
                "state_after": entry.state_after,
                "ok": entry.ok,
                "detail": entry.detail,
                "entry_hash": entry.hash,
                "prev_hash": entry.prev_hash,
                "seq": entry.seq,
                "timestamp": entry.ts,
                "agent_name": "agentguard_seer",
                "status": "SUCCESS" if entry.ok else "FAILED",
                "action_type": "observe",
            }
        )

    verdict_event = {
        "event_type": "seer_verdict",
        "run_id": run_id,
        "agent_name": "agentguard_seer",
        "action_type": "act",
        "status": "SUCCESS",
        "verdict_status": verdict.get("status"),
        "root_cause": verdict.get("root_cause"),
        "recommendation": verdict.get("recommendation"),
        "auditor_ok": verdict.get("auditor_ok"),
        "focus_agent": verdict.get("agent_name") or context.get("focus_agent"),
        "primary_trace_id": verdict.get("primary_trace_id"),
        "ledger_head": ledger.head_hash,
        "timestamp": time.time(),
        "remediation_ok": (verdict.get("remediation") or {}).get("ok"),
    }
    events.append(verdict_event)

    published = 0
    hec_error = None
    if hec:
        published, hec_error = _emit_hec(events)

    return {
        "run_id": run_id,
        "events": len(events),
        "published": published,
        "hec": bool(hec),
        "hec_error": hec_error,
        "verdict_status": verdict.get("status"),
        "ledger_head": ledger.head_hash,
        "preview": events[-1],
    }


def _emit_hec(events: list[Dict[str, Any]]) -> tuple[int, Optional[str]]:
    hec_url = os.environ.get("SPLUNK_HEC_URL", "").strip()
    hec_token = os.environ.get("SPLUNK_HEC_TOKEN", "").strip()
    if not hec_url or not hec_token:
        return 0, "SPLUNK_HEC_URL/TOKEN not set — skipped"

    try:
        from agentguard.exporters.splunk_hec import SplunkHECExporter
    except ImportError:
        # Fallback: lightweight POST
        return _emit_hec_raw(events, hec_url, hec_token)

    index = os.environ.get("SPLUNK_INDEX", "main")
    verify = os.environ.get("SPLUNK_VERIFY_SSL", "0").strip() == "1"
    exporter = SplunkHECExporter(
        hec_url=hec_url,
        hec_token=hec_token,
        index=index,
        sourcetype="agentguard:seer",
        source="agentguard-seer",
        verify_ssl=verify,
    )
    n = 0
    for ev in events:
        try:
            exporter.export(ev)
            n += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("HEC publish failed: %s", exc)
            return n, str(exc)
    return n, None


def _emit_hec_raw(
    events: list[Dict[str, Any]],
    hec_url: str,
    hec_token: str,
) -> tuple[int, Optional[str]]:
    import requests

    base = hec_url.rstrip("/")
    url = (
        f"{base}/event"
        if base.endswith("/services/collector")
        else f"{base}/services/collector/event"
    )
    headers = {
        "Authorization": f"Splunk {hec_token}",
        "Content-Type": "application/json",
    }
    index = os.environ.get("SPLUNK_INDEX", "main")
    verify = os.environ.get("SPLUNK_VERIFY_SSL", "0").strip() == "1"
    n = 0
    for ev in events:
        payload = {
            "time": ev.get("timestamp", time.time()),
            "index": index,
            "sourcetype": "agentguard:seer",
            "source": "agentguard-seer",
            "event": ev,
        }
        try:
            resp = requests.post(
                url,
                headers=headers,
                data=json.dumps(payload),
                timeout=5.0,
                verify=verify,
            )
            if resp.status_code not in (200, 201):
                return n, f"HEC {resp.status_code}: {resp.text[:200]}"
            n += 1
        except requests.RequestException as exc:
            return n, str(exc)
    return n, None
