"""Verification — the coverage-driven layer (SPEC-13).

A parallel path to the measurement layer: it never rewrites the scoring engine or
the promotion gate. M40 ships assertions; later milestones add coverage models,
constrained-random stimulus, formal proof over the authorization layer, and
sign-off.

Importing this package registers the built-in assertion library.
"""

from agenttic.verification.assertions import (  # noqa: F401
    ASSERTIONS, AssertionResult, AssertionStatus, assertion, evaluate,
    exercised_ratio, summarize, unexercised, verdict_for, violations)
from agenttic.verification.builtins import DEFAULT_ASSERTION_IDS  # noqa: F401

__all__ = [
    "ASSERTIONS", "AssertionResult", "AssertionStatus", "assertion", "evaluate",
    "exercised_ratio", "summarize", "unexercised", "verdict_for", "violations",
    "DEFAULT_ASSERTION_IDS",
]
