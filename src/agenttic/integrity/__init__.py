"""Suite integrity (SPEC-6): mechanical gates that verify our own instruments.

A benchmark is a measuring instrument, and instruments must themselves be
verified. Before a suite can be approved it must pass three gates — oracle
(solvable), dummy (non-vacuous), exploit (cheat-resistant) — or a human must
waive a named gate with a recorded reason.
"""

from agenttic.integrity.agents import ExploitAgent, NullAgent
from agenttic.integrity.gates import run_integrity_gates, verify_suite

__all__ = ["ExploitAgent", "NullAgent", "run_integrity_gates", "verify_suite"]
