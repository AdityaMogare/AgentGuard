"""AgentGuard Seer — governed agent-native investigation loop (kassi-style).

Failure spike → localize → correlate → analyze → audit → remediate → publish.
The driver sees one tool: ``step(action, inputs)``. Illegal moves are refused
and every move is hash-chained for ``agentguard-verify``.
"""

from .fsm import InvestigationMachine, InvestigationState
from .ledger import Ledger, verify_ledger

__all__ = [
    "InvestigationMachine",
    "InvestigationState",
    "Ledger",
    "verify_ledger",
    "__version__",
]

__version__ = "0.1.0"
