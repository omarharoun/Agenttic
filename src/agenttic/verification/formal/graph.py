"""The policy graph — the tool-authorization layer as a finite state machine
(SPEC-13 Step 63).

**Scope discipline first.** We do NOT verify the model. We verify the
*deterministic guard layer* around it: the tool-authorization state machine
defined by the autonomy policy (SPEC-2's compiled ``EnforcementPolicy``) and the
policy document (SPEC-7). That layer is finite, and reachability over it is
decidable — which is exactly why a claim about it can be categorically stronger
than any sampled benchmark, and exactly why the claim must state its limit.

States are ``(permission, confirmation, entity, tenant, availability)``. Edges
are tool invocations with guards. Nothing here reasons about what the model will
*choose* to do — only about what the guard layer *permits* it to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Iterator

ActionClass = str            # "read" | "write"


@dataclass(frozen=True, order=True)
class GuardState:
    """A reachable configuration of the authorization layer."""

    authenticated: bool = False
    #: tools whose irreversible action has been explicitly confirmed
    confirmed: frozenset[str] = frozenset()
    #: an entity has been read into the session (a write needs one)
    entity_loaded: bool = False
    #: the tenant scope currently bound to the session ("" = none bound)
    tenant: str = ""
    #: access revoked (terminal for tool use)
    revoked: bool = False

    def label(self) -> str:
        return (f"auth={int(self.authenticated)} entity={int(self.entity_loaded)} "
                f"tenant={self.tenant or '-'} confirmed={sorted(self.confirmed) or '-'}"
                f"{' REVOKED' if self.revoked else ''}")


@dataclass(frozen=True)
class ToolEdge:
    """One tool invocation, with the guards the policy places on it."""

    tool: str
    action_class: ActionClass = "read"
    requires_auth: bool = True
    requires_confirmation: bool = False
    requires_entity: bool = False
    #: the tenant this call touches ("" = the session's own tenant)
    touches_tenant: str = ""
    denied: bool = False               # policy denies it outright
    revokes: bool = False              # e.g. terminate_session / revoke_access
    grants_auth: bool = False          # e.g. an authenticate step
    loads_entity: bool = False         # a read that binds an entity
    confirms: str = ""                 # a confirmation step for a named tool
    binds_tenant: str = ""             # binds the session to a tenant

    def enabled_in(self, s: GuardState) -> bool:
        """Does the GUARD LAYER permit this edge from this state?"""
        if self.denied or s.revoked:
            return False
        if self.requires_auth and not s.authenticated:
            return False
        if self.requires_entity and not s.entity_loaded:
            return False
        if self.requires_confirmation and self.tool not in s.confirmed:
            return False
        return True

    def apply(self, s: GuardState) -> GuardState:
        out = s
        if self.grants_auth:
            out = replace(out, authenticated=True)
        if self.loads_entity:
            out = replace(out, entity_loaded=True)
        if self.confirms:
            out = replace(out, confirmed=out.confirmed | {self.confirms})
        if self.binds_tenant:
            out = replace(out, tenant=self.binds_tenant)
        if self.revokes:
            out = replace(out, revoked=True)
        return out


@dataclass
class PolicyGraph:
    """The finite transition system. ``unbounded`` marks a graph whose state
    space is not finite (e.g. an unbounded counter), so exhaustive reachability
    is not a decision procedure and the result must be ``unbounded``."""

    edges: list[ToolEdge] = field(default_factory=list)
    initial: GuardState = GuardState()
    unbounded: bool = False
    source: str = ""                   # what this was extracted from

    def successors(self, s: GuardState) -> Iterator[tuple[ToolEdge, GuardState]]:
        for e in self.edges:
            if e.enabled_in(s):
                yield e, e.apply(s)

    def tool(self, name: str) -> ToolEdge | None:
        return next((e for e in self.edges if e.tool == name), None)


# --------------------------------------------------------------------------- #
# extraction from the real compiled policy
# --------------------------------------------------------------------------- #

_WRITE_HINTS = ("issue", "refund", "transfer", "delete", "update", "create",
                "charge", "cancel", "send", "remove", "write", "purge")


def _action_class(tool: str, declared: str = "") -> ActionClass:
    if declared in ("read", "write"):
        return declared
    return "write" if any(h in tool.lower() for h in _WRITE_HINTS) else "read"


def from_enforcement_policy(policy, *, tools: Iterable[str] | None = None,
                            confirmable: Iterable[str] | None = None) -> PolicyGraph:
    """Extract the guard FSM from a compiled ``EnforcementPolicy`` (SPEC-2).

    Rule actions map onto guards: ``deny`` removes the edge, ``require_approval``
    makes it need an explicit confirmation, ``terminate_session`` /
    ``revoke_access`` move to the revoked state, ``allow`` leaves it guarded only
    by authentication."""
    names = list(tools) if tools is not None else []
    by_tool: dict[str, str] = {}          # tool -> strongest action
    _rank = {"allow": 0, "transform": 1, "require_approval": 2, "deny": 3,
             "terminate_session": 4, "revoke_access": 4}
    for rule in getattr(policy, "rules", []) or []:
        t = (rule.matcher or {}).get("tool") or (rule.matcher or {}).get("tool_name")
        if not t:
            continue
        if t not in names:
            names.append(t)
        prev = by_tool.get(t)
        if prev is None or _rank.get(rule.action, 0) > _rank.get(prev, 0):
            by_tool[t] = rule.action

    edges: list[ToolEdge] = [
        ToolEdge(tool="authenticate", action_class="read", requires_auth=False,
                 grants_auth=True),
    ]
    for t in names:
        action = by_tool.get(t, "allow")
        cls = _action_class(t)
        if action in ("terminate_session", "revoke_access"):
            edges.append(ToolEdge(tool=t, action_class=cls, revokes=True))
            continue
        edges.append(ToolEdge(
            tool=t, action_class=cls,
            requires_auth=True,
            requires_confirmation=(action == "require_approval"),
            requires_entity=(cls == "write"),
            denied=(action == "deny"),
            loads_entity=(cls == "read"),
        ))
    for t in (confirmable or []):
        edges.append(ToolEdge(tool=f"confirm::{t}", action_class="read",
                              requires_auth=True, confirms=t))
    return PolicyGraph(edges=edges, initial=GuardState(),
                       source=getattr(policy, "policy_id", "enforcement-policy"))
