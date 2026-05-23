import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional

from .exporters.base import SpanExporter
from .exporters.backend import BackendExporter
from .exporters.splunk_hec import SplunkHECExporter

logger = logging.getLogger("agentguard")


class AgentGuardClient:
    """Singleton client: queues span events and flushes to all configured exporters."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._exporters: List[SpanExporter] = []
        self.queue: queue.Queue = queue.Queue(maxsize=10000)
        self.shutdown_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self._initialized = True

    def add_exporter(self, exporter: SpanExporter) -> None:
        self._exporters.append(exporter)

    def clear_exporters(self) -> None:
        self._exporters.clear()

    def start_worker(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.shutdown_event.clear()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def stop_worker(self, timeout: float = 2.0) -> None:
        self.shutdown_event.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=timeout)

    def log_span(self, event: Dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            logger.warning("AgentGuard span queue full; dropping event.")

    def _worker_loop(self) -> None:
        while not self.shutdown_event.is_set() or not self.queue.empty():
            try:
                event = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            for exporter in self._exporters:
                try:
                    exporter.export(event)
                except Exception as exc:
                    logger.error("Exporter %s failed: %s", type(exporter).__name__, exc)
            self.queue.task_done()

    @property
    def exporter_count(self) -> int:
        return len(self._exporters)


_client: Optional[AgentGuardClient] = None


def configure(
    *,
    splunk_hec_url: Optional[str] = None,
    splunk_hec_token: Optional[str] = None,
    splunk_index: str = "main",
    splunk_verify_ssl: bool = True,
    backend_url: Optional[str] = "http://localhost:8000",
    api_key: Optional[str] = None,
    enable_splunk: bool = True,
    enable_backend: bool = True,
) -> AgentGuardClient:
    """
    Configure global client and exporters.
    Reads SPLUNK_HEC_URL / SPLUNK_HEC_TOKEN from env when args omitted.
    """
    import os

    global _client
    client = get_client()
    client.clear_exporters()

    hec_url = splunk_hec_url or os.environ.get("SPLUNK_HEC_URL")
    hec_token = splunk_hec_token or os.environ.get("SPLUNK_HEC_TOKEN")
    backend = backend_url or os.environ.get("AGENTGUARD_BACKEND_URL", "http://localhost:8000")
    key = api_key or os.environ.get("AGENTGUARD_API_KEY")

    if enable_splunk and hec_url and hec_token:
        client.add_exporter(
            SplunkHECExporter(
                hec_url=hec_url,
                hec_token=hec_token,
                index=splunk_index,
                verify_ssl=splunk_verify_ssl,
            )
        )
    elif enable_splunk:
        logger.info("Splunk HEC not configured (set SPLUNK_HEC_URL and SPLUNK_HEC_TOKEN).")

    if enable_backend and backend:
        client.add_exporter(BackendExporter(api_url=backend, api_key=key))

    client.start_worker()
    _client = client
    return client


def get_client() -> AgentGuardClient:
    global _client
    if _client is None:
        _client = AgentGuardClient()
        _client.start_worker()
    return _client
