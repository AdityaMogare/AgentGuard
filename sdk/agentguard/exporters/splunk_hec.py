import json
import logging
import time
from typing import Any, Dict, Optional

import requests

from .base import SpanExporter

logger = logging.getLogger("agentguard")


class SplunkHECExporter(SpanExporter):
    """
    Posts span events to Splunk HTTP Event Collector.
    https://docs.splunk.com/Documentation/Splunk/latest/Data/UsetheHTTPEventCollector
    """

    def __init__(
        self,
        hec_url: str,
        hec_token: str,
        index: str = "main",
        sourcetype: str = "agentguard:trace",
        source: str = "agentguard-sdk",
        verify_ssl: bool = True,
    ):
        base = hec_url.rstrip("/")
        if base.endswith("/services/collector"):
            self._url = f"{base}/event"
        else:
            self._url = f"{base}/services/collector/event"
        self._token = hec_token
        self._index = index
        self._sourcetype = sourcetype
        self._source = source
        self._verify_ssl = verify_ssl

    def export(self, event: Dict[str, Any]) -> None:
        payload = {
            "time": event.get("timestamp", time.time()),
            "host": event.get("agent_name", "agentguard"),
            "index": self._index,
            "sourcetype": self._sourcetype,
            "source": self._source,
            "event": event,
        }
        headers = {
            "Authorization": f"Splunk {self._token}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                self._url,
                headers=headers,
                data=json.dumps(payload),
                timeout=5.0,
                verify=self._verify_ssl,
            )
            if response.status_code not in (200, 201):
                logger.warning(
                    "Splunk HEC returned %s: %s",
                    response.status_code,
                    response.text[:500],
                )
        except requests.RequestException as exc:
            logger.error("Splunk HEC export failed: %s", exc)
