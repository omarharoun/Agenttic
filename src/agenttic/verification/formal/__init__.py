"""Formal verification of the tool-authorization layer (SPEC-13 Step 63).

We verify the deterministic GUARD LAYER, never the model. Every rendered claim
carries its scope limitation in the same sentence.

Two distinct questions are asked of the same guard layer, and they are never
merged (Hard Rule 72):

``prove``         — proof(authorization): can the agent's *actions* violate
                    policy? Four-valued: proven / counterexample / unbounded /
                    not_attempted.
``check_output``  — proof(claim), Step 63b: are the agent's *words* true about
                    that policy? Five-valued: valid / invalid / satisfiable /
                    ambiguous / impossible.
"""

from agenttic.verification.formal.claims import (  # noqa: F401
    ClaimCheck, ClaimResult, ClaimStatus, OutOfScope, PolicyClaim, check_claim,
    check_output, policy_conflicts, translate)
# the two renderers are deliberately NOT interchangeable: proof(claim) and
# proof(authorization) answer different questions and never share a report row
# (Hard Rule 72), so the claim renderer keeps a distinct name.
from agenttic.verification.formal.claims import (  # noqa: F401
    render_report as render_claim_report)
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
           "render_report", "z3_available",
           "ClaimCheck", "ClaimResult", "ClaimStatus", "OutOfScope",
           "PolicyClaim", "check_claim", "check_output", "policy_conflicts",
           "translate", "render_claim_report"]
