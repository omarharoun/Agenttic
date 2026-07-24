"""Scenario space for `conversational_transactional` (SPEC-13 Step 60).

Dimensions align 1:1 with the coverage model's coverpoints, minus `trajectory` —
trajectory is an OUTPUT of the run, never an input you can ask for. Asking for a
trajectory shape would be stimulus/trace conflation at the source.
"""

from __future__ import annotations

from agenttic.stimulus.space import (
    Dimension, Illegal, Implies, ScenarioSpace)


def seed_space(version: int = 1) -> ScenarioSpace:
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
            Dimension("session_shape", ("single_turn", "multi_turn",
                                        "resumed_with_memory")),
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
