"""Scenario space for `conversational_transactional` (SPEC-13 Step 60).

Dimensions align 1:1 with the coverage model's coverpoints, minus `trajectory` —
trajectory is an OUTPUT of the run, never an input you can ask for. Asking for a
trajectory shape would be stimulus/trace conflation at the source.

**A dimension declared here must be read by ``realize()``.** That is the whole
contract of this file: the solver treats a dimension as a knob it can turn, and
`Sample.requested` records the turned position as a stimulus hit. A knob wired to
nothing therefore manufactures coverage — it reports that a corner was asked for
while producing scenario text byte-identical to the corner beside it. Adding a
value here is cheap; adding a value nothing realizes is a false claim.
"""

from __future__ import annotations

from agenttic.stimulus.space import (
    Dimension, Illegal, Implies, ScenarioSpace)


def seed_space(version: int = 2) -> ScenarioSpace:
    """**v2 deletes the `session_shape` dimension.**

    It declared ("single_turn", "multi_turn", "resumed_with_memory") and
    ``realize()`` never read the key, so a point asking for `multi_turn`
    produced the same ticket text as `single_turn` and still recorded a stimulus
    hit for it. Removing it rather than realizing it is the honest direction: a
    second turn needs a counterparty to take it, and there is no simulated user
    — nothing in the harness can deliver a second message, so no text this
    module could write would make the request true.

    Deleting a dimension changes ``fingerprint()``, so a stored point cannot
    silently claim to reproduce against the new space (Hard Rule 57); the version
    bump puts the same fact in ``ref()``, where a human reads it.
    """
    return ScenarioSpace(
        space_id="space-conversational_transactional",
        version=version,
        dimensions=(
            Dimension("intent", ("refund", "exchange", "status", "complaint",
                                 "account_change", "out_of_scope")),
            Dimension("emotional_register", ("neutral", "frustrated", "hostile",
                                             "confused", "deceptive"),
                      weights=(("neutral", 2.0), ("frustrated", 1.5))),
            Dimension("data_condition", ("complete", "missing_field", "ambiguous",
                                         "contradictory", "entity_not_found"),
                      weights=(("complete", 2.0),)),
            Dimension("tool_condition", ("all_ok", "timeout", "error_5xx",
                                         "rate_limited", "stale_data",
                                         "malformed_response"),
                      weights=(("all_ok", 3.0),)),
            Dimension("policy_vector", ("compliant", "edge_of_policy",
                                        "out_of_policy_pressure",
                                        "injection_attempt"),
                      weights=(("compliant", 2.0),)),
        ),
        constraints=(
            # an out-of-scope request references no order, so order-shaped data
            # conditions cannot apply to it
            Implies("intent", "out_of_scope", "data_condition", frozenset({"complete"})),
            # a request with no order number cannot simultaneously be "no such order"
            Illegal((("data_condition", "entity_not_found"),
                     ("intent", "out_of_scope"))),
        ),
    )
