import threading
import queue
import requests
import logging
import time

logger = logging.getLogger("promptops")

class PromptOpsClient:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(PromptOpsClient, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, api_url: str = "http://localhost:8000", api_key: str = None):
        if self._initialized:
            return
        
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.queue = queue.Queue(maxsize=10000)
        self.worker_thread = None
        self.shutdown_event = threading.Event()
        self._initialized = True
        self.start_worker()

    def start_worker(self):
        """Starts the daemon thread to send traces in the background."""
        self.shutdown_event.clear()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def stop_worker(self, timeout=2.0):
        """Stops the daemon worker and flushes remaining items."""
        self.shutdown_event.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=timeout)

    def log_trace(self, payload: dict):
        """Adds a trace payload to the queue."""
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            logger.warning("PromptOps trace queue full. Dropping telemetry.")

    def _worker_loop(self):
        """Continuous loop running in a background thread."""
        while not self.shutdown_event.is_set() or not self.queue.empty():
            try:
                # Block for a short time to wait for items
                payload = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                self._send_payload_to_api(payload)
            except Exception as e:
                logger.error(f"Error uploading trace to PromptOps: {e}")
            finally:
                self.queue.task_done()

    def _send_payload_to_api(self, payload: dict):
        """Sends a POST request to the backend REST API with retries."""
        url = f"{self.api_url}/api/v1/traces/ingest/"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Api-Key {self.api_key}"

        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=5.0)
                if response.status_code in [200, 201]:
                    return
                else:
                    logger.warning(
                        f"PromptOps API returned status {response.status_code}: {response.text}"
                    )
            except requests.RequestException as e:
                if attempt == max_retries - 1:
                    raise e
                time.sleep(0.5)

# Global Client reference
_client = None

def configure(api_url: str = "http://localhost:8000", api_key: str = None):
    """Configures the global PromptOps Client."""
    global _client
    _client = PromptOpsClient(api_url=api_url, api_key=api_key)

def get_client() -> PromptOpsClient:
    """Returns the global client. Auto-initializes if not configured."""
    global _client
    if _client is None:
        _client = PromptOpsClient()
    return _client
