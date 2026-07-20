# AgentGuard Implementation Plan

> **Pivot:** PromptOps (prompt eval CI) → AgentGuard (multi-agent observability in Splunk)

## One-sentence pitch

AgentGuard instruments multi-agent AI systems with a lightweight Python SDK, streams execution traces into Splunk via HEC, surfaces failure patterns in real-time SPL dashboards, and exposes a Splunk MCP server so Claude can query telemetry to explain why agents failed.

---

## Phases & status

| Phase | Scope | Status |
|-------|--------|--------|
| **1** | SDK: `agentguard`, `context.py`, exporters, `@trace_agent` / `@trace_tool` | ✅ Done |
| **2** | Backend: `AgentRun`, `Span`, span ingest API, agent list/detail | ✅ Done |
| **3** | Demo: psutil agents, `run_demo.py`, `inject_failure.py` | ✅ Done |
| **4** | Splunk: HEC env docs, `splunk_app/` saved searches + dashboard | ✅ Done |
| **5** | MCP: 3 tools + Splunk job polling + Django fallback | ✅ Done |
| **6** | Polish: README, PyPI `agentguard`, optional React | 🔄 README done; PyPI manual; React optional |
| **7** | Hackathon: AI Assistant NL→SPL, MLTK/anomaly, smart alerts, demo script | ✅ Done |
| **8** | Production hardening (JWT / Postgres / Celery) | ✅ Done |

**Phase 7 files:** `mcp_server/ai_assistant.py`, `mcp_server/anomaly.py`, `splunk_app/mltk_setup.py`, `splunk_app/default/alerts.conf`, `sdk/agentguard/alert_handler.py`, `scripts/demo.py`, `scripts/setup.sh`

**Phase 8:** `DATABASE_URL` + composite/partial indexes, `ASYNC_SPAN_INGEST` + `ingest_span_task`, JWT + hashed `SDKApiKey`, `create_sdk_key`, `/api/v1/health/`, Compose `worker` profile

**Phase 9 files:** `seer/` — FSM + `step` MCP, hash-chained ledger + verify, windowed correlation, writer/auditor, remediation parse-gate, HEC publish (`agentguard:seer`)

**Deprioritized / removed:** Python eval engine, Dataset/EvalRun APIs, LurisQA demo, GitHub Actions eval CLI.

---

## Phase 8 — Production hardening ✅

See **[PRODUCTION_HARDENING_PLAN.md](PRODUCTION_HARDENING_PLAN.md)**. Delivered:

| Track | Implementation |
|-------|----------------|
| **Auth** | `SDKApiKey`, `SDKKeyAuthentication`, SimpleJWT, `create_sdk_key`, `/api/v1/auth/*` |
| **Database** | `DATABASE_URL`, migrations `0004`/`0005` indexes + partial failed-span index, `AgentMetricRollup` |
| **Async ingest** | `span_service.upsert_span`, `ingest_span_task`, `202` when `ASYNC_SPAN_INGEST=1`, Compose `--profile async` |

---

## Phase 9 — Seer (agent-native kassi)

Closed loop over AgentGuard traces (no k6 in v1):

1. Governed FSM with single `step` MCP tool (`seer/mcp_step.py`)
2. Hash-chained ledger + `python -m seer.verify`
3. Deterministic SPL correlation over wall-clock window + MLTK/anomaly
4. Writer + independent auditor → sealed verdict
5. Structured remediation → `ast` parse gate → unified diff
6. Publish investigation walk to HEC (`sourcetype=agentguard:seer`)

```bash
SPLUNK_MOCK=1 python -m seer investigate --persist --no-hec
python -m unittest seer.test_seer -v
```

---

## Phase 1 — SDK (`sdk/agentguard/`)

### Keep from PromptOps
- Background thread + queue flush
- Latency via `time.perf_counter()`
- Token/cost utils (for LLM tool steps)

### Add / modify
| File | Purpose |
|------|---------|
| `context.py` | `contextvars`: `trace_id`, `span_id`, `parent_span_id` |
| `exporters/base.py` | Exporter interface |
| `exporters/splunk_hec.py` | POST to Splunk HEC |
| `exporters/backend.py` | POST to Django `/api/v1/spans/ingest/` |
| `client.py` | Multi-exporter queue |
| `tracer.py` | `@trace_agent`, `@trace_tool` |
| `__init__.py` | `configure()`, public API |

### Span event schema
```json
{
  "trace_id": "uuid",
  "span_id": "uuid",
  "parent_span_id": "uuid|null",
  "agent_name": "cpu_monitor",
  "action_type": "observe|act|tool|error",
  "tool_name": "psutil.cpu_percent|null",
  "status": "SUCCESS|FAILED|TIMEOUT",
  "error_type": "TimeoutError|null",
  "latency_ms": 12.4,
  "input": {},
  "output": "...",
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "cost": 0.0,
  "timestamp": 1710000000.0
}
```

### Environment variables
| Variable | Default | Purpose |
|----------|---------|---------|
| `SPLUNK_HEC_URL` | — | e.g. `https://localhost:8088` |
| `SPLUNK_HEC_TOKEN` | — | HEC token |
| `AGENTGUARD_BACKEND_URL` | `http://localhost:8000` | Django mirror |
| `AGENTGUARD_API_KEY` | — | Optional API key |

---

## Phase 2 — Backend

### Models
- **AgentRun** — `trace_id` (PK), `agent_name`, `status`, `started_at`, `ended_at`
- **Span** — `span_id` (PK), FK `AgentRun`, span fields from schema above

### API
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/spans/ingest/` | SDK backend exporter |
| GET | `/api/v1/agents/` | List agents + failure stats |
| GET | `/api/v1/agents/{trace_id}/` | Span tree for one run |

Legacy PromptOps endpoints remain under `/api/v1/` until removed.

---

## Phase 3 — Demo (`demo/`)

Three psutil agents (no API keys):
1. `cpu_monitor` — CPU threshold alerts
2. `memory_monitor` — RAM usage
3. `disk_monitor` — disk usage

Scripts:
- `run_demo.py` — run all agents for N cycles
- `inject_failure.py` — force `FAILED` / `TIMEOUT` for Splunk dashboard demo

---

## Phase 4 — Splunk (`splunk_app/`)

Saved searches (SPL):
- `agentguard_failures_15m` — `status=FAILED` by `agent_name`
- `agentguard_latency_p95` — p95 `latency_ms` by agent
- `agentguard_error_breakdown` — top `error_type`

Dashboard XML: failure timeline, agent health table, span latency chart.

---

## Phase 5 — MCP (`mcp_server/server.py`)

| Tool | SPL / behavior |
|------|----------------|
| `search_agent_traces` | Last N min, filter `agent_name`, `status` |
| `explain_agent_failure` | Span tree + error for `trace_id` |
| `agent_health_summary` | Pass rate, timeouts, top errors |

Uses `mcp` + FastMCP; queries Splunk REST or mock when `SPLUNK_MOCK=1`.

---

## Build order (this session)

1. ✅ Plan doc
2. ✅ SDK package `agentguard`
3. ✅ Backend models + migrations (`0002_agentrun_span`) + span ingest + agent views + tests
4. ✅ Demo agents
5. ✅ MCP server (job polling, Django fallback) + splunk_app package
6. ✅ Root README

## Local dev quickstart

```bash
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && pip install -e sdk/
cd backend && python manage.py migrate && python manage.py runserver
# Another terminal:
AGENTGUARD_BACKEND_URL=http://localhost:8000 python demo/run_demo.py --backend-only
SPLUNK_MOCK=0 AGENTGUARD_BACKEND_URL=http://localhost:8000 python -c "
from mcp_server.server import search_agent_traces
print(search_agent_traces(status='FAILED'))
"
```

---

## Security note

Never commit PyPI tokens or Splunk HEC tokens. Use `.env` only.
