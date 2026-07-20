"""Governed investigation FSM — one legal step surface."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import actions, correlate, publish, remediate, verdict
from .ledger import Ledger


class InvestigationState(str, Enum):
    START = "START"
    DETECTED = "DETECTED"
    LOCALIZED = "LOCALIZED"
    CORRELATED = "CORRELATED"
    ANALYZED = "ANALYZED"
    AUDITED = "AUDITED"
    REMEDIATED = "REMEDIATED"
    PUBLISHED = "PUBLISHED"
    DONE = "DONE"


# action -> (from_states, to_state)
TRANSITIONS: Dict[str, Tuple[Set[InvestigationState], InvestigationState]] = {
    "detect": ({InvestigationState.START}, InvestigationState.DETECTED),
    "localize": ({InvestigationState.DETECTED}, InvestigationState.LOCALIZED),
    "correlate": ({InvestigationState.LOCALIZED}, InvestigationState.CORRELATED),
    "analyze": ({InvestigationState.CORRELATED}, InvestigationState.ANALYZED),
    "audit": ({InvestigationState.ANALYZED}, InvestigationState.AUDITED),
    "remediate": ({InvestigationState.AUDITED}, InvestigationState.REMEDIATED),
    "publish": (
        {InvestigationState.AUDITED, InvestigationState.REMEDIATED},
        InvestigationState.PUBLISHED,
    ),
    "finish": ({InvestigationState.PUBLISHED}, InvestigationState.DONE),
    # Early exits when no spike / no regression
    "clear": (
        {
            InvestigationState.DETECTED,
            InvestigationState.LOCALIZED,
            InvestigationState.CORRELATED,
            InvestigationState.AUDITED,
        },
        InvestigationState.DONE,
    ),
}


@dataclass
class StepResult:
    ok: bool
    refused: bool
    action: str
    state: str
    next_actions: List[str]
    detail: str
    data: Dict[str, Any] = field(default_factory=dict)
    entry_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "refused": self.refused,
            "action": self.action,
            "state": self.state,
            "next_actions": self.next_actions,
            "detail": self.detail,
            "data": self.data,
            "entry_hash": self.entry_hash,
        }


@dataclass
class InvestigationMachine:
    """Burr-like state machine with a single step() entrypoint."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: InvestigationState = InvestigationState.START
    ledger: Optional[Ledger] = None
    context: Dict[str, Any] = field(default_factory=dict)
    window_start: Optional[float] = None
    window_end: Optional[float] = None
    failure_rate_threshold: float = 10.0  # percent

    def __post_init__(self) -> None:
        if self.ledger is None:
            self.ledger = Ledger(run_id=self.run_id)
        else:
            self.ledger.run_id = self.run_id
        self.context.setdefault("run_id", self.run_id)
        self.window_start = self.window_start or time.time()

    def legal_actions(self) -> List[str]:
        return sorted(
            action
            for action, (sources, _) in TRANSITIONS.items()
            if self.state in sources
        )

    def step(self, action: str, inputs: Optional[Dict[str, Any]] = None) -> StepResult:
        inputs = dict(inputs or {})
        action = (action or "").strip().lower()
        before = self.state

        if action not in TRANSITIONS or before not in TRANSITIONS[action][0]:
            detail = (
                f"Illegal step '{action}' from {before.value}. "
                f"Valid next: {self.legal_actions()}"
            )
            entry = self.ledger.append(
                kind="refuse",
                action=action or "(empty)",
                state_before=before.value,
                state_after=before.value,
                inputs=inputs,
                outputs={"valid_next": self.legal_actions()},
                ok=False,
                detail=detail,
            )
            return StepResult(
                ok=False,
                refused=True,
                action=action,
                state=before.value,
                next_actions=self.legal_actions(),
                detail=detail,
                data={"valid_next": self.legal_actions()},
                entry_hash=entry.hash,
            )

        handler = _HANDLERS[action]
        try:
            data, detail, force_clear = handler(self, inputs)
        except Exception as exc:  # noqa: BLE001 — seal failure into ledger
            detail = f"{action} failed: {exc}"
            entry = self.ledger.append(
                kind="step",
                action=action,
                state_before=before.value,
                state_after=before.value,
                inputs=inputs,
                outputs={"error": str(exc)},
                ok=False,
                detail=detail,
            )
            return StepResult(
                ok=False,
                refused=False,
                action=action,
                state=before.value,
                next_actions=self.legal_actions(),
                detail=detail,
                data={"error": str(exc)},
                entry_hash=entry.hash,
            )

        target = (
            InvestigationState.DONE
            if force_clear
            else TRANSITIONS[action][1]
        )
        if force_clear and action != "clear":
            # detect/localize/correlate/audit may short-circuit to DONE
            self.state = InvestigationState.DONE
        else:
            self.state = target

        self.window_end = time.time()
        kind = "seal" if action == "finish" else "step"
        entry = self.ledger.append(
            kind=kind,
            action=action,
            state_before=before.value,
            state_after=self.state.value,
            inputs=inputs,
            outputs=data,
            ok=True,
            detail=detail,
        )
        return StepResult(
            ok=True,
            refused=False,
            action=action,
            state=self.state.value,
            next_actions=self.legal_actions(),
            detail=detail,
            data=data,
            entry_hash=entry.hash,
        )

    def run_to_completion(
        self,
        *,
        agent_name: Optional[str] = None,
        minutes: int = 60,
        remediate: bool = True,
        remediations: Optional[List[Dict[str, Any]]] = None,
        publish_hec: bool = True,
    ) -> Dict[str, Any]:
        """Drive the legal path autonomously (deterministic actions only)."""
        steps: List[Dict[str, Any]] = []
        plan = [
            ("detect", {"minutes": minutes, "agent_name": agent_name}),
            ("localize", {"minutes": minutes, "agent_name": agent_name}),
            ("correlate", {"minutes": minutes, "agent_name": agent_name}),
            ("analyze", {}),
            ("audit", {}),
        ]
        if remediate:
            plan.append(
                ("remediate", {"edits": remediations} if remediations is not None else {})
            )
        plan.append(("publish", {"hec": publish_hec}))
        plan.append(("finish", {}))

        for action, inp in plan:
            if self.state == InvestigationState.DONE:
                break
            if action not in self.legal_actions():
                # e.g. clear already taken
                continue
            result = self.step(action, inp)
            steps.append(result.to_dict())
            if result.refused or not result.ok:
                break
            if self.state == InvestigationState.DONE and action != "finish":
                break

        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "verdict": self.context.get("verdict"),
            "steps": steps,
            "ledger_head": self.ledger.head_hash,
            "ledger": self.ledger.dump(),
        }


Handler = Callable[..., Tuple[Dict[str, Any], str, bool]]


def _handle_detect(m: InvestigationMachine, inputs: Dict[str, Any]) -> tuple:
    minutes = int(inputs.get("minutes") or 60)
    agent_name = inputs.get("agent_name")
    rates = actions.failure_rates(minutes=minutes)
    m.context["failure_rates"] = rates
    m.context["detect_minutes"] = minutes
    if agent_name:
        m.context["focus_agent"] = agent_name

    spiked = []
    for row in rates.get("agents") or []:
        try:
            fr = float(row.get("failure_rate") or 0)
        except (TypeError, ValueError):
            fr = 0.0
        name = row.get("agent_name")
        if agent_name and name != agent_name:
            continue
        if fr >= m.failure_rate_threshold:
            spiked.append({**row, "failure_rate": fr})

    m.context["spiked_agents"] = spiked
    if not spiked:
        detail = (
            f"No failure spike above {m.failure_rate_threshold}% "
            f"in last {minutes}m — clearing."
        )
        m.context["verdict"] = {
            "status": "CLEAR",
            "reason": detail,
            "evidence": rates,
        }
        return {"spiked_agents": [], "rates": rates}, detail, True

    detail = f"Detected {len(spiked)} agent(s) over failure threshold."
    return {"spiked_agents": spiked, "rates": rates}, detail, False


def _handle_localize(m: InvestigationMachine, inputs: Dict[str, Any]) -> tuple:
    minutes = int(inputs.get("minutes") or m.context.get("detect_minutes") or 60)
    focus = inputs.get("agent_name") or m.context.get("focus_agent")
    spiked = m.context.get("spiked_agents") or []
    if not focus and spiked:
        focus = spiked[0].get("agent_name")

    traces = actions.search_failures(agent_name=focus, minutes=minutes)
    m.context["focus_agent"] = focus
    m.context["failure_traces"] = traces
    failed_ids = [
        r.get("trace_id")
        for r in (traces.get("results") or [])
        if r.get("trace_id")
    ]
    m.context["trace_ids"] = failed_ids[:20]

    if not failed_ids:
        detail = f"No FAILED traces for agent={focus} — clearing."
        m.context["verdict"] = {
            "status": "CLEAR",
            "reason": detail,
            "evidence": traces,
        }
        return {"agent_name": focus, "trace_ids": []}, detail, True

    # Pull span tree for top failing trace
    primary = failed_ids[0]
    tree = actions.explain_failure(primary)
    m.context["primary_trace_id"] = primary
    m.context["span_tree"] = tree
    detail = f"Localized to agent={focus}, primary_trace={primary}, {len(failed_ids)} failing traces."
    return {
        "agent_name": focus,
        "trace_ids": failed_ids,
        "primary_trace_id": primary,
        "span_tree_summary": tree.get("summary"),
    }, detail, False


def _handle_correlate(m: InvestigationMachine, inputs: Dict[str, Any]) -> tuple:
    minutes = int(inputs.get("minutes") or m.context.get("detect_minutes") or 60)
    agent = inputs.get("agent_name") or m.context.get("focus_agent")
    # Wall-clock window for exact SPL scoping
    end = time.time()
    start = end - (minutes * 60)
    m.window_start = start
    m.window_end = end

    corr = correlate.correlate_window(
        agent_name=agent,
        earliest_epoch=start,
        latest_epoch=end,
    )
    m.context["correlation"] = corr

    status = corr.get("verdict_hint") or "UNKNOWN"
    if status == "CLEAR":
        detail = "Correlation found no regression in window — clearing."
        m.context["verdict"] = {
            "status": "CLEAR",
            "reason": detail,
            "evidence": corr,
        }
        return corr, detail, True

    detail = (
        f"Correlated window [{int(start)}–{int(end)}]: "
        f"hint={status}, errors={corr.get('error_count')}, "
        f"anomalies={corr.get('anomaly_count')}."
    )
    return corr, detail, False


def _handle_analyze(m: InvestigationMachine, inputs: Dict[str, Any]) -> tuple:
    draft = verdict.write_analysis(m.context, model=inputs.get("model"))
    m.context["analysis"] = draft
    return draft, draft.get("summary") or "Analysis drafted.", False


def _handle_audit(m: InvestigationMachine, inputs: Dict[str, Any]) -> tuple:
    analysis = m.context.get("analysis") or {}
    audited = verdict.audit_analysis(
        analysis,
        evidence=m.context,
        model=inputs.get("model"),
    )
    m.context["audit"] = audited
    sealed = {
        "status": audited.get("status") or analysis.get("status") or "UNKNOWN",
        "root_cause": analysis.get("root_cause"),
        "recommendation": analysis.get("recommendation"),
        "evidence_cites": analysis.get("evidence_cites") or [],
        "auditor_ok": audited.get("ok", False),
        "auditor_notes": audited.get("notes"),
        "agent_name": m.context.get("focus_agent"),
        "primary_trace_id": m.context.get("primary_trace_id"),
        "correlation": m.context.get("correlation"),
        "run_id": m.run_id,
    }
    m.context["verdict"] = sealed

    if not audited.get("ok") and audited.get("reject_run"):
        detail = f"Auditor rejected analysis: {audited.get('notes')}"
        return audited, detail, True

    detail = (
        f"Auditor {'passed' if audited.get('ok') else 'flagged'} "
        f"analysis; status={sealed['status']}."
    )
    return {"audit": audited, "verdict": sealed}, detail, False


def _handle_remediate(m: InvestigationMachine, inputs: Dict[str, Any]) -> tuple:
    if "edits" in inputs and inputs["edits"] is not None:
        edits = inputs["edits"]
    else:
        edits = (m.context.get("analysis") or {}).get("proposed_edits") or []
    result = remediate.apply_edits(edits, dry_run=bool(inputs.get("dry_run", True)))
    m.context["remediation"] = result
    if m.context.get("verdict"):
        m.context["verdict"]["remediation"] = {
            "ok": result.get("ok"),
            "diff": result.get("unified_diff"),
            "files": result.get("files"),
        }
    detail = (
        "Remediation validated."
        if result.get("ok")
        else f"Remediation failed: {result.get('error')}"
    )
    return result, detail, False


def _handle_publish(m: InvestigationMachine, inputs: Dict[str, Any]) -> tuple:
    use_hec = inputs.get("hec", True)
    payload = publish.publish_run(
        run_id=m.run_id,
        ledger=m.ledger,
        verdict=m.context.get("verdict") or {},
        context=m.context,
        hec=bool(use_hec),
    )
    m.context["publish"] = payload
    return payload, "Published investigation walk + verdict.", False


def _handle_finish(m: InvestigationMachine, inputs: Dict[str, Any]) -> tuple:
    return {"run_id": m.run_id, "verdict": m.context.get("verdict")}, "Done.", False


def _handle_clear(m: InvestigationMachine, inputs: Dict[str, Any]) -> tuple:
    reason = inputs.get("reason") or "Operator cleared investigation."
    m.context["verdict"] = {
        "status": "CLEAR",
        "reason": reason,
        "run_id": m.run_id,
    }
    return m.context["verdict"], reason, False


_HANDLERS: Dict[str, Handler] = {
    "detect": _handle_detect,
    "localize": _handle_localize,
    "correlate": _handle_correlate,
    "analyze": _handle_analyze,
    "audit": _handle_audit,
    "remediate": _handle_remediate,
    "publish": _handle_publish,
    "finish": _handle_finish,
    "clear": _handle_clear,
}


def default_ledger_path(run_id: str, base: Optional[Path] = None) -> Path:
    root = base or Path.cwd() / ".agentguard" / "ledgers"
    return root / f"{run_id}.jsonl"
