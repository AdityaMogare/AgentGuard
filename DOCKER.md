# Docker — run AgentGuard with one command

Share this repo with a teammate; they only need **Docker Desktop** (or Docker Engine + Compose v2).

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Mac/Windows) or Docker + Compose on Linux
- **Optional:** Splunk Enterprise on the host for live dashboards (not required — mock mode works)

## Quick start (no Splunk)

```bash
git clone <your-repo-url> AgentGuard
cd AgentGuard

cp .env.docker.example .env
docker compose up --build -d

# Wait ~30s, then load demo data
docker compose run --rm demo

# Open API
open http://localhost:8001/api/v1/agents/
```

Root health check: http://localhost:8001/

## What runs in Docker

| Service | Port | Purpose |
|---------|------|---------|
| **api** | 8001 | Django + Gunicorn (span ingest, agents API, alerts) |
| **db** | (internal) | PostgreSQL 16 — persistent telemetry |
| **redis** | (internal) | Celery broker (ready for async ingest later) |
| **demo** | one-shot | `docker compose run --rm demo` injects 150 spans |

Splunk is **not** containerized by default (large image + license). Use Splunk on the host or skip it with `SPLUNK_MOCK=1`.

## Common commands

```bash
# Start stack
docker compose up -d

# View logs
docker compose logs -f api

# Inject demo spans
docker compose run --rm demo

# Run psutil agent demo (5 cycles)
docker compose --profile demo run --rm psutil-demo

# Stop everything
docker compose down

# Stop and delete database volume (fresh start)
docker compose down -v
```

## Connect Splunk on your host machine

If Splunk runs **outside** Docker on the same laptop:

1. Edit `.env`:

```bash
SPLUNK_MOCK=0
SPLUNK_HEC_URL=https://host.docker.internal:8088
SPLUNK_HEC_TOKEN=<your-hec-token>
SPLUNK_HOST=https://host.docker.internal:8089
SPLUNK_REST_TOKEN=<splunk-session-key>
SPLUNK_VERIFY_SSL=0
```

2. Restart API and run demo **with** Splunk export:

```bash
docker compose up -d --build
docker compose --profile demo run --rm psutil-demo
# Edit psutil-demo command in compose to drop --backend-only, or run:
docker compose run --rm --entrypoint /entrypoint.sh api psutil-demo --cycles 5
```

3. Splunk Web stays at **http://localhost:8000** (host). AgentGuard API stays at **http://localhost:8001**.

## Claude MCP (runs on host, not in Docker)

Claude Desktop launches MCP via stdio on your machine. Point it at the Dockerized API:

**`~/Library/Application Support/Claude/claude_desktop_config.json`:**

```json
{
  "mcpServers": {
    "agentguard": {
      "command": "docker",
      "args": [
        "compose",
        "-f",
        "/absolute/path/to/AgentGuard/docker-compose.yml",
        "run",
        "--rm",
        "-T",
        "api",
        "mcp"
      ],
      "env": {
        "SPLUNK_MOCK": "1",
        "AGENTGUARD_BACKEND_URL": "http://api:8001"
      }
    }
  }
}
```

**Simpler (recommended):** install Python locally and run:

```bash
cd AgentGuard
pip install -r requirements.txt && pip install -e sdk/
AGENTGUARD_BACKEND_URL=http://localhost:8001 SPLUNK_MOCK=1 python mcp_server/server.py
```

## API endpoints (friend cheat sheet)

| URL | Description |
|-----|-------------|
| http://localhost:8001/ | Service info |
| http://localhost:8001/api/v1/agents/ | List agent runs |
| http://localhost:8001/api/v1/spans/ingest/ | POST spans (SDK) |
| http://localhost:8001/api/v1/alerts/webhook/ | Splunk alert webhook |
| http://localhost:8001/admin/ | Django admin |

Create admin user:

```bash
docker compose run --rm api shell createsuperuser
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Port 8001 in use | Set `API_PORT=8002` in `.env` |
| Database connection errors | `docker compose down -v && docker compose up --build` |
| Empty agents list | Run `docker compose run --rm demo` |
| Splunk HEC fails from container | Use `host.docker.internal` not `localhost` in HEC URL |
| Claude can't reach API | Use `http://localhost:8001` on host, not `http://api:8001` |

## Production notes

- Set `DEBUG=0` and a strong `DJANGO_SECRET_KEY` in `.env`
- Set `AGENTGUARD_API_KEY` for ingest auth
- Put a reverse proxy (nginx) in front of Gunicorn for TLS
- See [PRODUCTION_HARDENING_PLAN.md](PRODUCTION_HARDENING_PLAN.md) for JWT, async Celery ingest
