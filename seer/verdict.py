"""Writer + independent auditor for grounded investigation narratives.

Deterministic scaffold always produces a cited analysis from correlation
evidence. Optional LLM backends can enrich the narrative when configured;
auditor remains a separate pass with stricter grounding checks.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional


def write_analysis(
    context: Dict[str, Any],
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Draft root-cause analysis. Model hint is optional; scaffold is authoritative."""
    corr = context.get("correlation") or {}
    tree = context.get("span_tree") or {}
    focus = context.get("focus_agent")
    primary = context.get("primary_trace_id")
    hint = corr.get("verdict_hint") or "UNKNOWN"
    top = corr.get("top_error") or {}
    failed_spans = tree.get("failed_spans") or []

    error_type = top.get("error_type") or _first(failed_spans, "error_type") or "unknown"
    tool_name = top.get("tool_name") or _first(failed_spans, "tool_name") or ""

    cites: List[str] = []
    if primary:
        cites.append(f"trace:{primary}")
    if focus:
        cites.append(f"agent:{focus}")
    if corr.get("spl"):
        cites.append("spl:correlation_window")
    if corr.get("anomaly_count"):
        cites.append(f"anomaly_count:{corr.get('anomaly_count')}")

    if hint == "REGRESSION":
        root = (
            f"Agent '{focus}' shows a server-side regression: "
            f"{corr.get('error_count', 0)} failing spans "
            f"({corr.get('failure_rate', 0)}% failure rate) "
            f"dominated by {error_type}"
            + (f" in tool '{tool_name}'" if tool_name else "")
            + "."
        )
        recommendation = (
            f"Inspect tool '{tool_name or 'unknown'}' error handling for {error_type}; "
            "add timeout/retry bounds and validate inputs before the tool call."
        )
    elif hint == "DEGRADING":
        root = (
            f"Agent '{focus}' is degrading: p95 latency "
            f"{corr.get('p95_latency')}ms with {corr.get('anomaly_count', 0)} "
            "anomalous buckets and little/no hard errors."
        )
        recommendation = (
            "Profile the hottest tool spans for N+1 or unbounded work; "
            "cap concurrency and cache repeated lookups."
        )
    else:
        root = f"No clear regression for agent '{focus}' in the investigation window."
        recommendation = "Continue monitoring; no remediation required."

    proposed = _propose_edits(context, error_type=error_type, tool_name=tool_name, hint=hint)

    draft = {
        "status": hint,
        "root_cause": root,
        "recommendation": recommendation,
        "evidence_cites": cites,
        "evidence": {
            "failure_rate": corr.get("failure_rate"),
            "error_count": corr.get("error_count"),
            "p95_latency": corr.get("p95_latency"),
            "top_error": top,
            "primary_trace_id": primary,
            "span_summary": tree.get("summary"),
        },
        "proposed_edits": proposed,
        "writer": model or os.environ.get("AGENTGUARD_SEER_WRITER", "scaffold"),
    }

    enriched = _maybe_llm_enrich(draft, context, role="writer", model=model)
    if enriched:
        draft["root_cause"] = enriched.get("root_cause") or draft["root_cause"]
        draft["recommendation"] = enriched.get("recommendation") or draft["recommendation"]
        draft["writer"] = enriched.get("model") or draft["writer"]
    return draft


def audit_analysis(
    analysis: Dict[str, Any],
    *,
    evidence: Dict[str, Any],
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Independent groundedness check — refuses uncited or contradicted claims."""
    corr = evidence.get("correlation") or {}
    hint = corr.get("verdict_hint")
    status = analysis.get("status")
    cites = analysis.get("evidence_cites") or []
    notes: List[str] = []
    ok = True
    reject_run = False

    if not cites:
        ok = False
        notes.append("Analysis has no evidence citations.")

    if hint and status and hint != status and not (
        hint == "UNKNOWN" or status == "UNKNOWN"
    ):
        ok = False
        notes.append(f"Status '{status}' disagrees with correlation hint '{hint}'.")

    # Numeric claims must not invent errors when correlation says zero
    root = analysis.get("root_cause") or ""
    if (corr.get("error_count") or 0) == 0 and re.search(
        r"\b\d+\s+fail", root, re.I
    ):
        ok = False
        notes.append("Narrative claims failures but correlation error_count=0.")

    if hint == "CLEAR" and status in ("REGRESSION", "DEGRADING"):
        ok = False
        reject_run = True
        notes.append("Cannot page on CLEAR correlation.")

    # Optional second model
    llm = _maybe_llm_enrich(
        {"analysis": analysis, "correlation": corr},
        evidence,
        role="auditor",
        model=model or os.environ.get("AGENTGUARD_SEER_AUDITOR"),
    )
    if llm and llm.get("ok") is False:
        ok = False
        notes.append(llm.get("notes") or "LLM auditor rejected analysis.")

    return {
        "ok": ok,
        "reject_run": reject_run,
        "notes": "; ".join(notes) if notes else "Grounded.",
        "status": status if ok else hint or status,
        "auditor": model or os.environ.get("AGENTGUARD_SEER_AUDITOR", "scaffold"),
    }


def _propose_edits(
    context: Dict[str, Any],
    *,
    error_type: str,
    tool_name: str,
    hint: str,
) -> List[Dict[str, Any]]:
    """Heuristic structured edits targeting demo agents when paths are known."""
    if hint == "CLEAR":
        return []

    focus = context.get("focus_agent") or "agent"
    # Demo agents live in a single module; prefer that path when present.
    path = "demo/agents/monitors.py"
    if error_type in ("TimeoutError", "TIMEOUT") or hint == "DEGRADING":
        return [
            {
                "path": path,
                "action": "insert_if_missing",
                "marker": "AGENTGUARD_TOOL_TIMEOUT_SEC",
                "text": (
                    f"\n# AgentGuard seer remediation for {focus}/{tool_name or 'tool'}\n"
                    f"AGENTGUARD_TOOL_TIMEOUT_SEC = 30.0  # bound unbounded / tight timeouts\n"
                ),
                "rationale": f"Bound timeouts related to {error_type} on {tool_name or focus}.",
            }
        ]
    if error_type:
        return [
            {
                "path": path,
                "action": "insert_if_missing",
                "marker": "AGENTGUARD_ERROR_GUARD",
                "text": (
                    f"\n# AGENTGUARD_ERROR_GUARD: handle {error_type} from {tool_name or 'tool'}\n"
                    f"def _agentguard_guard_{focus}(exc: Exception) -> None:\n"
                    f"    if type(exc).__name__ == {error_type!r}:\n"
                    f"        raise RuntimeError({error_type!r} + ' guarded') from exc\n"
                    f"    raise\n"
                ),
                "rationale": f"Add explicit guard for {error_type}.",
            }
        ]
    return []


def _first(rows: List[Dict[str, Any]], key: str) -> Optional[str]:
    for r in rows:
        val = r.get(key)
        if val:
            return str(val)
    return None


def _maybe_llm_enrich(
    draft: Dict[str, Any],
    context: Dict[str, Any],
    *,
    role: str,
    model: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Optional OpenAI-compatible enrichment. Failures return None (scaffold wins)."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or os.environ.get("AGENTGUARD_SEER_LLM", "0") != "1":
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        use_model = model or os.environ.get("AGENTGUARD_SEER_MODEL", "gpt-4o-mini")
        prompt = (
            f"You are the {role} for AgentGuard seer. "
            "Respond with JSON only. Stay grounded in the evidence; do not invent metrics.\n"
            f"DRAFT/INPUT:\n{json.dumps(draft, default=str)[:6000]}\n"
            f"CONTEXT_KEYS:\n{json.dumps(list(context.keys()))}"
        )
        resp = client.chat.completions.create(
            model=use_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content or "{}"
        data = json.loads(text)
        data["model"] = use_model
        return data
    except Exception:
        return None
