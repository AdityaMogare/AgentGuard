import logging
import time
from typing import Any, Dict, Optional

import requests

from .base import SpanExporter

logger = logging.getLogger("agentguard")


class BackendExporter(SpanExporter):
    """Mirrors spans to the AgentGuard Django API (optional dev / fallback)."""

    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
    ):
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key

    def export(self, event: Dict[str, Any]) -> None:
        url = f"{self._api_url}/api/v1/spans/ingest/"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Api-Key {self._api_key}"

        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    url, json={"span": event}, headers=headers, timeout=5.0
                )
                if response.status_code in (200, 201):
                    return
                logger.warning(
                    "Backend export status %s: %s",
                    response.status_code,
                    response.text[:300],
                )
            except requests.RequestException as exc:
                if attempt == max_retries - 1:
                    logger.error("Backend export failed: %s", exc)
                else:
                    time.sleep(0.5)
