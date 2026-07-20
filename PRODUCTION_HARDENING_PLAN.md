# AgentGuard Production Hardening Plan

> **Goal:** Implement three capabilities that align with production-grade observability claims:
> 1. REST auth with JWT + hashed SDK keys  
> 2. PostgreSQL with optimized indexes for agent telemetry  
> 3. Celery + Redis async pipeline for continuous span ingest  

**Current state (baseline):**

| Area | Today | Gap |
|------|--------|-----|
| Auth | Plain `Api-Key` env compare ([`permissions.py`](backend/api/permissions.py)) | No JWT, no hashed keys, no per-tenant keys |
| Database | SQLite ([`settings.py`](backend/promptops_backend/settings.py)) | No PostgreSQL |
| Indexes | Basic composite indexes on `AgentRun` / `Span` | No PG-specific tuning, no time-series partitioning |
| Async ingest | Synchronous `SpanIngestView.post()` | Celery only used for legacy `execute_evaluation` ([`tasks.py`](backend/api/tasks.py)) |
| SDK queue | Thread + `queue.Queue` in [`client.py`](sdk/agentguard/client.py) | Not connected to Celery |

**Recommended build order:** Phase 2 (PostgreSQL) → Phase 3 (Celery ingest) → Phase 1 (auth).  
Reason: auth and async both touch ingest; stable DB schema first avoids rework.

---

## Phase 1 — JWT + hashed SDK keys

### 1.1 Data model

**New file:** `backend/api/auth_models.py`

```python
class SDKApiKey(models.Model):
    id = UUIDField(primary_key=True)
    name = CharField(max_length=100)           # e.g. "prod-ingest", "demo-agent"
    key_prefix = CharField(max_length=12)      # first 8 chars for lookup (ag_xxxx)
    key_hash = CharField(max_length=128)       # bcrypt or PBKDF2 hash of full secret
    is_active = BooleanField(default=True)
    created_at = DateTimeField(auto_now_add=True)
    last_used_at = DateTimeField(null=True)
    scopes = JSONField(default=list)           # ["spans:write", "agents:read"]
```

**Migration:** `0004_sdkapikey.py`

**Key format:** `ag_<prefix>_<random32>` — store only hash; show full key once at creation (admin CLI or API).

### 1.2 Hashing utilities

**New file:** `backend/api/auth_utils.py`

- `generate_sdk_key() -> tuple[plain, prefix, hash]`
- `verify_sdk_key(plain: str, stored_hash: str) -> bool` using `django.contrib.auth.hashers.make_password` / `check_password` (PBKDF2, no new deps) or `bcrypt` if added to `requirements.txt`
- Never log or persist plaintext keys

### 1.3 Authentication classes (DRF)

**New file:** `backend/api/authentication.py`

| Class | Header | Use case |
|-------|--------|----------|
| `SDKKeyAuthentication` | `Authorization: Api-Key ag_...` | SDK span ingest, webhooks |
| `JWTAuthentication` | `Authorization: Bearer <jwt>` | Dashboard users, MCP proxy, admin read APIs |

**JWT:** add `djangorestframework-simplejwt` to `requirements.txt`.

**Settings additions:**

```python
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "api.authentication.JWTAuthentication",
        "api.authentication.SDKKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}
```

**New endpoints:**

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/auth/token/` | username/password or service account | Issue JWT |
| POST | `/api/v1/auth/token/refresh/` | refresh token | Refresh JWT |
| POST | `/api/v1/auth/keys/` | JWT (admin scope) | Create SDK key (returns plaintext once) |
| GET | `/api/v1/auth/keys/` | JWT | List keys (prefix only) |
| DELETE | `/api/v1/auth/keys/{id}/` | JWT | Revoke key |

### 1.4 Custom decorators + permissions

**New file:** `backend/api/decorators.py`

Function decorators for non-DRF views (if any):

```python
@require_scopes("spans:write")
def my_view(request): ...
```

**Extend** `backend/api/permissions.py`:

- `HasScopePermission` — checks `request.auth.scopes`
- `IsJWTUserOrSDKKey` — ingest allows SDK key; list agents requires JWT or `agents:read` scope on key
- Deprecate env-based `AGENTGUARD_API_KEY` (keep as dev fallback when `DEBUG=True` and no keys in DB)

### 1.5 Wire existing views

| View | New auth |
|------|----------|
| `SpanIngestView` | `SDKKeyAuthentication` + `HasScope("spans:write")` |
| `AlertWebhookView` | SDK key with `alerts:write` OR shared webhook secret header |
| `AgentRunViewSet` | JWT or SDK key with `agents:read` |

### 1.6 SDK changes

**Files:** [`sdk/agentguard/exporters/backend.py`](sdk/agentguard/exporters/backend.py), [`client.py`](sdk/agentguard/client.py)

- Continue sending `Authorization: Api-Key <key>` (no change to wire format)
- Document key rotation: env `AGENTGUARD_API_KEY` → generated `ag_...` keys
- Optional: JWT support for read-only CLI tools (not required for ingest)

### 1.7 Tests

**File:** `backend/api/test_auth.py`

- Create key → hash stored, plaintext not in DB
- Valid key ingests span; invalid/revoked → 401
- JWT login → list agents works; no scope → 403
- Env fallback in DEBUG mode

### 1.8 Acceptance criteria

- [x] No plaintext SDK secrets in database or git
- [x] Span ingest requires valid SDK key in production (`DEBUG=False`) when keys exist
- [x] JWT protects key-management endpoints
- [x] Resume claim defensible: *"API-key auth with hashed secrets + JWT for operator access"*

---

## Phase 2 — PostgreSQL + index optimization

### 2.1 Dependencies & config

**Add to** `requirements.txt`:

```
psycopg[binary]>=3.1
dj-database-url>=2.0
```

**Update** `backend/promptops_backend/settings.py`:

```python
import dj_database_url
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}
```

**`.env.example` additions:**

```bash
DATABASE_URL=postgresql://agentguard:password@localhost:5432/agentguard
```

**Local dev:** Docker Compose service `postgres:16` (optional `docker-compose.yml`).

### 2.2 Schema review & new indexes

Current indexes in [`agent_models.py`](backend/api/agent_models.py) are a good start. Add PostgreSQL-oriented indexes:

**Migration `0005_pg_indexes.py`:**

```python
# AgentRun — list/filter by time + agent (dashboard queries)
Index(fields=["-started_at", "agent_name"], name="idx_run_started_agent")
Index(fields=["status", "-started_at"], name="idx_run_status_started")

# Span — time-range scans, failure analysis
Index(fields=["-created_at", "status"], name="idx_span_created_status")
Index(fields=["agent_name", "-created_at"], name="idx_span_agent_created")
Index(fields=["trace_id via agent_run"], ...)  # FK already indexed

# Partial index (PostgreSQL only) — hot path for failures
RunSQL(
    "CREATE INDEX idx_span_failed_recent ON api_span (created_at DESC) "
    "WHERE status IN ('FAILED', 'TIMEOUT');",
    reverse_sql="DROP INDEX IF EXISTS idx_span_failed_recent;"
)
```

**Optional:** `AgentMetricRollup` model (hourly aggregates) to avoid full table scans:

```python
class AgentMetricRollup(models.Model):
    agent_name = CharField(db_index=True)
    hour = DateTimeField(db_index=True)
    span_count = IntegerField()
    failure_count = IntegerField()
    avg_latency_ms = FloatField()
    total_cost = FloatField()
    class Meta:
        unique_together = [("agent_name", "hour")]
```

Populate via Celery periodic task (Phase 3).

### 2.3 Query optimizations

**Refactor** `AgentRunViewSet.list()`:

- Replace N+1 aggregate queries with single annotated queryset:

```python
AgentRun.objects.annotate(
    avg_latency=Avg("spans__latency_ms"),
    total_cost=Sum("spans__cost"),
).order_by("-started_at")[:100]
```

- Add pagination (`PageNumberPagination`, default 50)
- Add filters: `?agent_name=`, `?status=`, `?since=` (django-filter optional)

### 2.4 Migration path SQLite → PostgreSQL

1. `python manage.py dumpdata api.AgentRun api.Span api.AgentAlert --indent 2 -o backup.json`
2. Point `DATABASE_URL` at PostgreSQL
3. `python manage.py migrate`
4. `python manage.py loaddata backup.json`
5. Verify counts match; run ingest smoke test

### 2.5 Tests

- Migration applies on PostgreSQL (CI service container)
- Explain plan on slow query: `\d api_span`, `EXPLAIN ANALYZE` for list + failure filter
- Partial index used for `status=FAILED` query (PG only)

### 2.6 Acceptance criteria

- [x] Production uses PostgreSQL via `DATABASE_URL`
- [x] Composite + partial indexes documented in migration
- [x] Agent list API uses annotated queryset (no N+1); optional `?page=` pagination
- [x] Resume claim defensible: *"PostgreSQL schema with composite/partial indexes for telemetry queries"*

---

## Phase 3 — Celery + Redis async span ingest

### 3.1 Architecture

```mermaid
flowchart LR
    SDK[agentguard SDK] -->|POST /spans/ingest| API[Django API]
    API -->|enqueue| Redis[(Redis broker)]
    Redis --> Worker[Celery worker]
    Worker --> PG[(PostgreSQL)]
    Worker -->|optional| SplunkHEC[Splunk HEC retry]
```

**Principle:** HTTP handler validates auth + schema, returns **202 Accepted** quickly; worker persists span + updates aggregates.

### 3.2 New Celery tasks

**Extend** `backend/api/tasks.py`:

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=2)
def ingest_span_task(self, span_payload: dict, received_at: str):
    """Idempotent span write — same logic as SpanIngestView today."""
    ...

@shared_task
def rollup_agent_metrics(hour_iso: str):
    """Hourly aggregation into AgentMetricRollup."""

@shared_task
def flush_stale_runs():
    """Mark RUNNING runs older than N minutes as TIMEOUT."""
```

Extract shared logic from `SpanIngestView` into `backend/api/span_service.py`:

- `upsert_span(event: dict) -> IngestResult`
- Used by both sync path (dev) and Celery task (prod)

### 3.3 API changes

**Update** `SpanIngestView.post()`:

```python
if settings.ASYNC_SPAN_INGEST:  # env ASYNC_SPAN_INGEST=1
    ingest_span_task.delay(event)
    return Response({"message": "accepted", "async": True}, status=202)
return _sync_ingest(event)  # existing behavior
```

**New endpoint (optional batch):**

`POST /api/v1/spans/ingest/batch/` — list of spans, one Celery task per batch chunk (max 100).

### 3.4 Redis & Celery configuration

**Update** `settings.py`:

```python
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "0") == "1"
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TASK_ROUTES = {
    "api.tasks.ingest_span_task": {"queue": "spans"},
    "api.tasks.rollup_agent_metrics": {"queue": "metrics"},
}
CELERY_BEAT_SCHEDULE = {
    "rollup-hourly": {
        "task": "api.tasks.rollup_agent_metrics",
        "schedule": crontab(minute=5),
    },
}
```

**Add** `django-celery-beat` for scheduled rollups (optional).

**New file:** `docker-compose.yml` (dev)

```yaml
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: agentguard
      POSTGRES_USER: agentguard
      POSTGRES_PASSWORD: agentguard
    ports: ["5432:5432"]
  worker:
    build: .
    command: celery -A promptops_backend worker -Q spans,metrics -l info
    depends_on: [redis, postgres]
```

### 3.5 Idempotency & backpressure

- **Idempotency:** `update_or_create` on `span_id` (already in place) — safe for retries
- **Dedup:** optional Redis key `span:{span_id}` TTL 5m to skip duplicate enqueue
- **Backpressure:** if queue depth > threshold, return 503 with `Retry-After` (monitor via Redis `LLEN`)

### 3.6 SDK compatibility

No SDK change required for basic async — still POST same JSON. Optional improvements:

- Accept `202` as success in [`backend.py`](sdk/agentguard/exporters/backend.py)
- Retry on 503 with exponential backoff (already has 2 retries)

### 3.7 Observability of the pipeline

- Log task duration + queue lag
- Django admin or `/api/v1/health/` exposing Redis ping + queue depth
- Celery Flower (dev only) on `:5555`

### 3.8 Tests

**File:** `backend/api/test_async_ingest.py`

- `CELERY_TASK_ALWAYS_EAGER=True` in tests — task runs inline
- POST span → 202 → span exists in DB
- Duplicate span_id → single row
- Failed task retries (mock broker)

### 3.9 Acceptance criteria

- [x] Ingest returns 202 when `ASYNC_SPAN_INGEST=1` (enqueue only)
- [x] Worker processes spans via `ingest_span_task` (Compose `--profile async`)
- [x] Redis required when async; eager mode for tests/dev
- [x] Resume claim defensible: *"Celery + Redis async pipeline for continuous agent event ingest"*

---

## Implementation schedule (suggested)

| Week | Phase | Deliverables |
|------|-------|--------------|
| 1 | Phase 2 | PostgreSQL, migrations, indexes, query refactor, docker-compose |
| 2 | Phase 3 | `span_service.py`, Celery task, 202 ingest, worker + beat, tests |
| 3 | Phase 1 | SDK key model, JWT, auth endpoints, wire views, SDK docs, revoke flow |

**Hackathon / demo shortcut (1–2 days):**

1. PostgreSQL via Docker + `DATABASE_URL`
2. `ingest_span_task` + `ASYNC_SPAN_INGEST=1` + one worker process
3. Single hashed SDK key via management command `python manage.py create_sdk_key`

Skip JWT until operator UI is needed; use one scoped API key for demo.

---

## Files to create / modify (checklist)

### New files

| File | Phase |
|------|-------|
| `backend/api/auth_models.py` | 1 |
| `backend/api/auth_utils.py` | 1 |
| `backend/api/authentication.py` | 1 |
| `backend/api/decorators.py` | 1 |
| `backend/api/auth_views.py` | 1 |
| `backend/api/span_service.py` | 3 |
| `backend/api/test_auth.py` | 1 |
| `backend/api/test_async_ingest.py` | 3 |
| `docker-compose.yml` | 2 |
| `backend/api/management/commands/create_sdk_key.py` | 1 |

### Modified files

| File | Phase |
|------|-------|
| `backend/promptops_backend/settings.py` | 1, 2, 3 |
| `backend/api/permissions.py` | 1 |
| `backend/api/agent_views.py` | 1, 3 |
| `backend/api/tasks.py` | 3 |
| `backend/api/urls.py` | 1 |
| `backend/api/agent_models.py` | 2 |
| `requirements.txt` | 1, 2 |
| `.env.example` | 1, 2, 3 |
| `sdk/agentguard/exporters/backend.py` | 3 |
| `README.md` | all |

---

## Environment variables (final)

```bash
# Database
DATABASE_URL=postgresql://agentguard:agentguard@localhost:5432/agentguard

# Auth
JWT_SECRET_KEY=...                    # or use Django SECRET_KEY
AGENTGUARD_LEGACY_API_KEY=            # dev-only fallback

# Async
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_TASK_ALWAYS_EAGER=0
ASYNC_SPAN_INGEST=1
```

---

## Security notes

- Rotate SDK keys via admin API; audit `last_used_at`
- JWT refresh tokens httpOnly if browser clients added later
- Webhook endpoint: separate `X-Webhook-Secret` or scoped SDK key — do not reuse ingest keys
- Never commit `.env`; document key creation in README only

---

## Resume alignment (after completion)

| Claim | Backed by |
|-------|-----------|
| JWT + hashed SDK keys | `SDKApiKey` model, `SDKKeyAuthentication`, `simplejwt` |
| PostgreSQL + indexes | `DATABASE_URL`, migrations with composite/partial indexes |
| Celery + Redis event pipeline | `ingest_span_task`, Redis broker, 202 ingest API |
