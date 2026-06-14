"""Process Splunk alert webhooks and optional standalone listener."""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("agentguard.alerts")


def parse_webhook_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Splunk or demo webhook JSON into AgentGuard alert fields."""

    def pick(*keys: str, default: str = "") -> str:
        for key in keys:
            val = payload.get(key)
            if val not in (None, ""):
                return str(val)
        result = payload.get("result")
        if isinstance(result, dict):
            for key in keys:
                val = result.get(key)
                if val not in (None, ""):
                    return str(val)
        return default

    trace_raw = pick("trace_id", "traceId")
    trace_id: Optional[str] = None
    if trace_raw:
        try:
            trace_id = str(uuid.UUID(trace_raw))
        except ValueError:
            trace_id = trace_raw

    return {
        "alert_name": pick("alert_name", "search_name", "name", default="agentguard_alert"),
        "severity": pick("severity", "alert_severity", default="medium"),
        "agent_name": pick("agent_name", "agent"),
        "trace_id": trace_id,
        "message": pick("message", "description", "reason", default="Splunk alert fired"),
        "payload": payload,
    }


def forward_to_backend(parsed: Dict[str, Any], backend_url: Optional[str] = None) -> Dict[str, Any]:
    """POST parsed alert to Django webhook endpoint."""
    base = (backend_url or os.environ.get("AGENTGUARD_BACKEND_URL", "http://localhost:8001")).rstrip(
        "/"
    )
    url = f"{base}/api/v1/alerts/webhook/"
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("AGENTGUARD_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"

    body = {**parsed.get("payload", {}), **{k: v for k, v in parsed.items() if k != "payload" and v}}
    resp = requests.post(url, json=body, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


class _WebhookHandler(BaseHTTPRequestHandler):
    backend_url: str = "http://localhost:8001"

    def log_message(self, format: str, *args) -> None:
        logger.info(format % args)

    def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON"})
            return

        parsed = parse_webhook_payload(payload)
        try:
            result = forward_to_backend(parsed, self.backend_url)
            self._send_json(201, {"message": "forwarded", "backend": result})
        except Exception as exc:
            logger.error("Webhook forward failed: %s", exc)
            self._send_json(502, {"error": str(exc), "parsed": parsed})

    def do_GET(self) -> None:
        self._send_json(200, {"status": "ok", "service": "agentguard-alert-handler"})


def run_webhook_server(
    host: str = "0.0.0.0",
    port: int = 8765,
    backend_url: Optional[str] = None,
) -> HTTPServer:
    """Start a lightweight proxy that forwards Splunk webhooks to Django."""
    backend = backend_url or os.environ.get("AGENTGUARD_BACKEND_URL", "http://localhost:8001")
    handler_cls = type("Handler", (_WebhookHandler,), {"backend_url": backend})
    server = HTTPServer((host, port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("AgentGuard alert handler listening on http://%s:%s → %s", host, port, backend)
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("AGENTGUARD_ALERT_PORT", "8765"))
    server = run_webhook_server(port=port)
    print(f"Alert handler on http://0.0.0.0:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
