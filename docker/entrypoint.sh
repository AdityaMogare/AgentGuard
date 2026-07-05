#!/usr/bin/env bash
set -euo pipefail

cd /app/backend

wait_for_db() {
  if [ -z "${DATABASE_URL:-}" ]; then
    return 0
  fi
  echo "Waiting for database..."
  for i in $(seq 1 30); do
    if python -c "
import os, sys
url = os.environ.get('DATABASE_URL', '')
if not url.startswith('postgres'):
    sys.exit(0)
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'promptops_backend.settings')
django.setup()
from django.db import connection
connection.ensure_connection()
" 2>/dev/null; then
      echo "Database is ready."
      return 0
    fi
    sleep 2
  done
  echo "Database not ready after 60s" >&2
  exit 1
}

run_migrate() {
  python manage.py migrate --noinput
}

case "${1:-api}" in
  api)
    wait_for_db
    run_migrate
    echo "Starting AgentGuard API on :8001"
    exec gunicorn promptops_backend.wsgi:application \
      --bind 0.0.0.0:8001 \
      --workers "${GUNICORN_WORKERS:-2}" \
      --timeout 120 \
      --access-logfile - \
      --error-logfile -
    ;;
  demo)
    wait_for_db
    echo "Running demo (backend=${AGENTGUARD_BACKEND_URL:-http://api:8001})"
    cd /app
    exec python scripts/demo.py "${@:2}"
    ;;
  psutil-demo)
    cd /app
    exec python demo/run_demo.py "${@:2}"
    ;;
  mcp)
    cd /app
    exec python mcp_server/server.py
    ;;
  shell)
    wait_for_db
    run_migrate
    shift
    exec python manage.py "$@"
    ;;
  migrate)
    wait_for_db
    exec python manage.py migrate --noinput
    ;;
  *)
    exec "$@"
    ;;
esac
