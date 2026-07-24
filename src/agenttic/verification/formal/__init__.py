"""Formal verification of the tool-authorization layer (SPEC-13 Step 63).

We verify the deterministic GUARD LAYER, never the model. The result is
four-valued — proven / counterexample / unbounded / not_attempted — and every
rendered claim carries its scope limitation in the same sentence.
"""

from agenttic.verification.formal.graph import (  # noqa: F401
    GuardState, PolicyGraph, ToolEdge, from_enforcement_policy)
from agenttic.verification.formal.properties import (  # noqa: F401
    SHIPPED, Property, no_cross_tenant_exposure, no_tool_after_revocation,
    no_tool_without_confirmation, no_write_from_unauthenticated,
    no_write_without_prior_read)
from agenttic.verification.formal.prove import (  # noqa: F401
    ProofResult, ProofStatus, assert_scoped, prove, prove_all, render_report,
    z3_available)

__all__ = ["GuardState", "PolicyGraph", "ToolEdge", "from_enforcement_policy",
           "Property", "SHIPPED", "no_cross_tenant_exposure",
           "no_tool_after_revocation", "no_tool_without_confirmation",
           "no_write_from_unauthenticated", "no_write_without_prior_read",
           "ProofResult", "ProofStatus", "assert_scoped", "prove", "prove_all",
           "render_report", "z3_available"]
