__version__ = "0.1.0"

from .client import configure, get_client, PromptOpsClient
from .tracer import trace, eval, get_registered_evals

__all__ = [
    "__version__",
    "configure",
    "get_client",
    "PromptOpsClient",
    "trace",
    "eval",
    "get_registered_evals",
]
