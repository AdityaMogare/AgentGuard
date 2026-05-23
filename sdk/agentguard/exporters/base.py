from abc import ABC, abstractmethod
from typing import Any, Dict


class SpanExporter(ABC):
    """Sends a single span event to a destination (Splunk HEC, Django API, etc.)."""

    @abstractmethod
    def export(self, event: Dict[str, Any]) -> None:
        pass

    def flush(self) -> None:
        """Optional flush hook for batch exporters."""
        pass
