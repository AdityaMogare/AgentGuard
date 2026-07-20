# AgentGuard Seer

Governed agent-native investigation loop (kassi-style) on top of AgentGuard telemetry.

**Failure spike → localize → correlate → analyze → audit → remediate → publish**

The driving agent sees one tool, `step(action, inputs)`. Illegal moves are refused. Every step and refusal is hash-chained; `python -m seer.verify` proves the trail.

## Quick start (mock Splunk)

```bash
# Full autonomous walk
SPLUNK_MOCK=1 python -m seer investigate --persist --no-hec

# Verify the ledger
python -m seer.verify .agentguard/ledgers/<run_id>.jsonl

# Governed MCP server (single step tool + status + run_investigation)
SPLUNK_MOCK=1 python -m seer.mcp_step
```

Live Splunk: set `SPLUNK_MOCK=0`, `SPLUNK_HEC_*`, `SPLUNK_REST_TOKEN` (omit `--no-hec` to publish the walk as `sourcetype=agentguard:seer`).

## Legal path

| Action | From | To |
|--------|------|-----|
| `detect` | START | DETECTED (or DONE if clear) |
| `localize` | DETECTED | LOCALIZED |
| `correlate` | LOCALIZED | CORRELATED |
| `analyze` | CORRELATED | ANALYZED |
| `audit` | ANALYZED | AUDITED |
| `remediate` | AUDITED | REMEDIATED |
| `publish` | AUDITED / REMEDIATED | PUBLISHED |
| `finish` | PUBLISHED | DONE |
| `clear` | several | DONE |

## Design notes

- **SPL is never model-authored** — `seer/correlate.py` builds windowed queries from wall-clock epochs.
- **Verdict hint is deterministic** from correlation numbers; the writer only narrates; an independent auditor checks citations.
- **Remediation** emits structured edits, re-parses with `ast`, then renders a unified diff (`dry_run=True` by default).
- Optional LLM enrichment: `AGENTGUARD_SEER_LLM=1` + `OPENAI_API_KEY`.

## Tests

```bash
SPLUNK_MOCK=1 python -m unittest seer.test_seer -v
```
