# AgentGuard — API, demo scripts, MCP server (single image)
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# psutil for demo agents; libpq for PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY sdk/ /app/sdk/
RUN pip install -e /app/sdk/

COPY backend/ /app/backend/
COPY mcp_server/ /app/mcp_server/
COPY scripts/ /app/scripts/
COPY demo/ /app/demo/
COPY splunk_app/ /app/splunk_app/

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /app/backend

EXPOSE 8001

ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]
