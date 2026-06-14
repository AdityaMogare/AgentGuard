"""Natural language → SPL with Splunk AI Assistant API + rule-based fallback."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import splunk_client

# Rule-based NL patterns (always available for demo / offline).
_RULES: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(r"\bfail(ed|ure|ures)?\b", re.I),
        'search {base} status=FAILED earliest=-24h | stats count by agent_name, error_type | sort -count',
    ),
    (
        re.compile(r"\blatency|slow|p95|performance\b", re.I),
        "search {base} earliest=-1h | stats p95(latency_ms) as p95_latency_ms avg(latency_ms) as avg_latency_ms by agent_name | sort -p95_latency_ms",
    ),
    (
        re.compile(r"\bhealth|summary|overview\b", re.I),
        'search {base} earliest=-1h | stats count as total count(eval(status="SUCCESS")) as success count(eval(status="FAILED" OR status="TIMEOUT")) as failures by agent_name',
    ),
    (
        re.compile(r"\btimeout\b", re.I),
        'search {base} status=TIMEOUT earliest=-24h | table _time agent_name trace_id error_type latency_ms | sort -_time',
    ),
    (
        re.compile(r"\bcost|token\b", re.I),
        "search {base} earliest=-24h | stats sum(cost) as total_cost sum(prompt_tokens) as prompt_tokens sum(completion_tokens) as completion_tokens by agent_name",
    ),
    (
        re.compile(r"\brecent|last\b", re.I),
        "search {base} earliest=-15m | table _time agent_name status trace_id latency_ms error_type | sort -_time | head 25",
    ),
]

DEFAULT_SPL = "search {base} earliest=-1h | head 50"


def ai_assistant_enabled() -> bool:
    return os.environ.get("SPLUNK_AI_ASSISTANT_ENABLED", "0").strip() == "1"


def ai_assistant_url() -> str:
    custom = os.environ.get("SPLUNK_AI_ASSISTANT_URL", "").strip()
    if custom:
        return custom.rstrip("/")
    return f"{splunk_client.splunk_host()}/services/agent/assistant/spl"


def rule_based_spl(question: str) -> str:
    base = splunk_client.trace_base_filter()
    for pattern, template in _RULES:
        if pattern.search(question):
            return template.format(base=base)
    return DEFAULT_SPL.format(base=base)


def call_ai_assistant(question: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Call Splunk AI Assistant for SPL generation.
    Returns (spl, error). spl is None on failure.
    """
    if not ai_assistant_enabled() or splunk_client.use_mock():
        return None, "AI Assistant disabled or SPLUNK_MOCK=1"

    try:
        session, host = splunk_client.splunk_session()
        url = ai_assistant_url()
        if not url.startswith("http"):
            url = f"{host}{url}"

        resp = session.post(
            url,
            json={"query": question, "context": {"sourcetype": "agentguard:trace"}},
            timeout=30,
        )
        if resp.status_code >= 400:
            return None, f"AI Assistant HTTP {resp.status_code}: {resp.text[:300]}"

        data = resp.json()
        spl = (
            data.get("spl")
            or data.get("search")
            or data.get("generated_spl")
            or (data.get("result") or {}).get("spl")
        )
        if spl and isinstance(spl, str):
            if "index=" not in spl and "sourcetype=" not in spl:
                spl = f"search {splunk_client.trace_base_filter()} | {spl.lstrip('| ')}"
            return spl.strip(), None
        return None, "AI Assistant response missing SPL field"
    except Exception as exc:
        return None, str(exc)


def nl_to_spl(question: str) -> Dict[str, Any]:
    """Convert natural language to SPL; AI first, rule-based fallback."""
    question = question.strip()
    if not question:
        return {"error": "question is required"}

    method = "rule_based"
    warning = None
    spl = rule_based_spl(question)

    ai_spl, ai_err = call_ai_assistant(question)
    if ai_spl:
        spl = ai_spl
        method = "ai_assistant"
    elif ai_err and ai_assistant_enabled():
        warning = f"AI Assistant unavailable, using rules: {ai_err}"

    return {
        "question": question,
        "spl": spl,
        "method": method,
        "warning": warning,
    }


def check_ai_features() -> Dict[str, Any]:
    """Report which Splunk AI / ML features are available."""
    mock = splunk_client.use_mock()
    mltk = False
    assistant = False
    notes: List[str] = []

    if mock:
        notes.append("SPLUNK_MOCK=1 — using local mock data for searches")
    else:
        try:
            mltk = splunk_client.check_mltk_installed()
            if not mltk:
                notes.append("MLTK not installed — anomalydetection fallback will be used")
        except Exception as exc:
            notes.append(f"MLTK check failed: {exc}")

        assistant = ai_assistant_enabled()
        if not assistant:
            notes.append("SPLUNK_AI_ASSISTANT_ENABLED=0 — nl_search uses rule-based SPL")

    return {
        "splunk_mock": mock,
        "mltk_installed": mltk,
        "ai_assistant_enabled": assistant,
        "ai_assistant_url": ai_assistant_url() if assistant else None,
        "anomaly_fallback": "anomalydetection (built-in)" if not mltk else "MLTK DensityFunction",
        "notes": notes,
    }


def nl_search(question: str, count: int = 50) -> Dict[str, Any]:
    """NL → SPL → execute search."""
    conversion = nl_to_spl(question)
    if "error" in conversion and "spl" not in conversion:
        return conversion

    spl = conversion["spl"]
    if splunk_client.use_mock():
        from . import anomaly as anomaly_mod

        return {
            **conversion,
            "source": "mock",
            "results": anomaly_mod.mock_traces(),
            "result_count": 3,
        }

    try:
        rows, source = splunk_client.run_search(spl, count=count)
        return {
            **conversion,
            "source": source,
            "results": rows,
            "result_count": len(rows),
        }
    except splunk_client.SearchError as exc:
        return {**conversion, "error": str(exc), "results": [], "result_count": 0}
