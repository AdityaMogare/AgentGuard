#!/usr/bin/env bash
# AgentGuard hackathon setup — env check, migrations, MLTK probe, alert handler hint.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=== AgentGuard Setup ==="

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
  echo "Loaded .env"
else
  echo "No .env found — copy .env.example and set Splunk tokens"
fi

# Python venv
if [ ! -d "$ROOT/venv" ]; then
  echo "Creating venv…"
  python3 -m venv "$ROOT/venv"
fi
# shellcheck disable=SC1091
source "$ROOT/venv/bin/activate"

pip install -q -r requirements.txt
pip install -q -e "$ROOT/sdk/"

echo ""
echo "--- Django backend ---"
cd "$ROOT/backend"
python manage.py migrate --noinput
echo "Migrations applied. Start with: python manage.py runserver 8001"

echo ""
echo "--- Splunk env ---"
for var in SPLUNK_HEC_URL SPLUNK_HEC_TOKEN SPLUNK_HOST SPLUNK_REST_TOKEN SPLUNK_TOKEN; do
  if [ -n "${!var:-}" ]; then
    echo "  $var=set"
  else
    echo "  $var=not set"
  fi
done

if [ "${SPLUNK_MOCK:-1}" = "1" ]; then
  echo "  SPLUNK_MOCK=1 (mock mode — demo works without Splunk)"
else
  echo "  SPLUNK_MOCK=0 (live Splunk)"
fi

echo ""
echo "--- MLTK check ---"
cd "$ROOT"
python splunk_app/mltk_setup.py || true

echo ""
echo "--- Splunk app install (manual) ---"
echo "  cp -R splunk_app/default \$SPLUNK_HOME/etc/apps/agentguard/default"
echo "  Copy alerts.conf + savedsearches.conf for smart alerting"

echo ""
echo "--- Alert webhook ---"
WEBHOOK="${AGENTGUARD_BACKEND_URL:-http://localhost:8001}/api/v1/alerts/webhook/"
echo "  Splunk alerts → POST $WEBHOOK"
echo "  Optional proxy: python -m agentguard.alert_handler  (port ${AGENTGUARD_ALERT_PORT:-8765})"

echo ""
echo "--- Run demo ---"
echo "  Terminal 1: cd backend && python manage.py runserver 8001"
echo "  Terminal 2: python scripts/demo.py"
echo "  Terminal 3: python mcp_server/server.py  (configure in Claude/Cursor MCP)"
echo ""
echo "Setup complete."
