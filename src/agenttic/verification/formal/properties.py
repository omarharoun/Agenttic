"""The property language (SPEC-13 Step 63).

Deliberately small: a property is a predicate over a single transition
``(state_before, edge, state_after)`` that must never hold on any reachable
transition. That keeps every property decidable by reachability over the finite
guard layer, and keeps the claim it licenses precise.

Each property carries the **scope** and **limit** sentences that must be printed
in the same breath as the claim (Hard Rule 62) — they are part of the property,
not a footnote a renderer might drop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agenttic.verification.formal.graph import GuardState, ToolEdge

#: violates(before, edge, after) -> True when this transition breaks the property
Violates = Callable[[GuardState, ToolEdge, GuardState], bool]

DEFAULT_LIMIT = (
    "this proves the guard layer, not the model; if the agent can reach tools "
    "outside this interface, the proof does not apply")


@dataclass
class Property:
    property_id: str
    description: str
    violates: Violates
    scope: str = "the tool-authorization layer"
    limit: str = DEFAULT_LIMIT
    #: properties over an unbounded domain can never be discharged by
    #: reachability and must report `unbounded` rather than `proven`
    requires_unbounded_reasoning: bool = False
    tags: list[str] = field(default_factory=list)

    def claim(self) -> str:
        """The claim WITH its scope, in one sentence. There is no API here that
        renders the claim without its limit."""
        return (f"For all reachable paths in {self.scope}, {self.description}. "
                f"Limit: {self.limit}.")


# --------------------------------------------------------------------------- #
# the shipped properties
# --------------------------------------------------------------------------- #

def no_tool_without_confirmation(tool: str) -> Property:
    """e.g. no refund is issued without a preceding confirmation."""
    def violates(before: GuardState, edge: ToolEdge, after: GuardState) -> bool:
        return edge.tool == tool and tool not in before.confirmed
    return Property(
        property_id=f"no_{tool}_without_confirmation",
        description=f"no path invokes {tool!r} without a preceding confirmation",
        violates=violates, tags=["confirmation"])


def no_write_from_unauthenticated() -> Property:
    def violates(before: GuardState, edge: ToolEdge, after: GuardState) -> bool:
        return edge.action_class == "write" and not before.authenticated
    return Property(
        property_id="no_write_from_unauthenticated",
        description="no path reaches a write tool from an unauthenticated state",
        violates=violates, tags=["authentication"])


def no_write_without_prior_read() -> Property:
    def violates(before: GuardState, edge: ToolEdge, after: GuardState) -> bool:
        return edge.action_class == "write" and not before.entity_loaded
    return Property(
        property_id="no_write_without_prior_read",
        description="no path writes an entity it has not first read",
        violates=violates, tags=["entity"])


def no_cross_tenant_exposure() -> Property:
    def violates(before: GuardState, edge: ToolEdge, after: GuardState) -> bool:
        return bool(edge.touches_tenant and before.tenant
                    and edge.touches_tenant != before.tenant)
    return Property(
        property_id="no_cross_tenant_exposure",
        description=("no path exposes one tenant's entity inside another "
                     "tenant's session"),
        violates=violates, tags=["tenancy"])


def no_tool_after_revocation() -> Property:
    def violates(before: GuardState, edge: ToolEdge, after: GuardState) -> bool:
        return before.revoked
    return Property(
        property_id="no_tool_after_revocation",
        description="no path invokes any tool after access has been revoked",
        violates=violates, tags=["revocation"])


def unbounded_rate_property() -> Property:
    """A property over an unbounded domain (call counts), included so the
    four-valued result is exercised honestly: reachability cannot discharge it,
    so it must report `unbounded` — never `proven`."""
    return Property(
        property_id="no_more_than_n_calls_ever",
        description=("no path invokes a tool more than N times, for all N "
                     "(an unbounded counter)"),
        violates=lambda b, e, a: False,
        requires_unbounded_reasoning=True, tags=["rate"])


SHIPPED = (
    no_write_from_unauthenticated,
    no_write_without_prior_read,
    no_cross_tenant_exposure,
    no_tool_after_revocation,
)
