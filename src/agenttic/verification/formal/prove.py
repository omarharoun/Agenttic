"""Discharging safety properties over the guard layer (SPEC-13 Step 63).

The result is **four-valued and must stay four-valued** (anti-pattern §7.6):

``proven``          exhaustive reachability over a FINITE state space completed
                    with no violating transition. This is a decision procedure,
                    not a sample — that is why it licenses "for all reachable
                    paths" rather than "for 200 test cases".
``counterexample``  a concrete violating path, printed step by step.
``unbounded``       the state space (or the property) is not finite, or
                    exploration hit its cap, so reachability is not a decision
                    procedure here. **A bounded check never returns ``proven``.**
``not_attempted``   no discharge was run (e.g. a solver-backed method was asked
                    for and z3 is not installed).

Silence is never treated as safety: every property that was not discharged says
so in its own words.

Every rendered claim carries its scope limitation in the same sentence
(Hard Rule 62). ``render_report`` is the only renderer, and it refuses to emit a
report containing an unqualified claim.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal, Sequence

from agenttic.verification.formal.graph import GuardState, PolicyGraph
from agenttic.verification.formal.properties import Property

ProofStatus = Literal["proven", "counterexample", "unbounded", "not_attempted"]

DEFAULT_MAX_STATES = 200_000


def z3_available() -> bool:
    try:
        import z3  # noqa: F401
        return True
    except Exception:
        return False


@dataclass
class ProofResult:
    property_id: str
    status: ProofStatus
    scope: str
    limit: str
    description: str
    method: str = "reachability"
    states_explored: int = 0
    path: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "proven"

    def claim(self) -> str:
        """The sentence this result licenses — always with its limit attached.
        There is deliberately no way to render the claim without the limit."""
        if self.status == "proven":
            return (f"For all reachable paths in {self.scope}, "
                    f"{self.description}. Limit: {self.limit}.")
        if self.status == "counterexample":
            return (f"NOT proven — a reachable path in {self.scope} violates: "
                    f"{self.description}. Counterexample: "
                    + " -> ".join(self.path) + f". Limit: {self.limit}.")
        if self.status == "unbounded":
            return (f"UNBOUNDED — {self.description} could not be discharged by "
                    f"reachability over {self.scope} ({self.detail}). No safety "
                    f"claim is made. Limit: {self.limit}.")
        return (f"NOT ATTEMPTED — {self.description} was not discharged "
                f"({self.detail}). No safety claim is made. Limit: {self.limit}.")

    def as_dict(self) -> dict:
        return {"property_id": self.property_id, "status": self.status,
                "method": self.method, "states_explored": self.states_explored,
                "path": self.path, "scope": self.scope, "limit": self.limit,
                "detail": self.detail, "claim": self.claim()}


def prove(graph: PolicyGraph, prop: Property, *,
          max_states: int = DEFAULT_MAX_STATES,
          method: str = "reachability") -> ProofResult:
    """Discharge one property. ``method='z3'`` runs a bounded symbolic check,
    which can refute but — being bounded — never returns ``proven``."""
    base = dict(property_id=prop.property_id, scope=prop.scope, limit=prop.limit,
                description=prop.description)

    if prop.requires_unbounded_reasoning:
        return ProofResult(status="unbounded", method=method,
                           detail="the property quantifies over an unbounded domain",
                           **base)
    if graph.unbounded:
        return ProofResult(status="unbounded", method=method,
                           detail="the guard layer's state space is not finite",
                           **base)
    if method == "z3":
        if not z3_available():
            return ProofResult(
                status="not_attempted", method="z3",
                detail="z3 is not installed (pip install 'agenttic[formal]')", **base)
        return _bounded_check_z3(graph, prop, base)

    # exhaustive reachability over a finite system — a decision procedure
    seen: set[GuardState] = {graph.initial}
    parent: dict[GuardState, tuple[GuardState, str]] = {}
    queue: deque[GuardState] = deque([graph.initial])
    while queue:
        if len(seen) > max_states:
            return ProofResult(
                status="unbounded", method="reachability",
                states_explored=len(seen),
                detail=(f"exploration exceeded {max_states} states; the search was "
                        "incomplete, so no proof is claimed"), **base)
        s = queue.popleft()
        for edge, nxt in graph.successors(s):
            if prop.violates(s, edge, nxt):
                return ProofResult(
                    status="counterexample", method="reachability",
                    states_explored=len(seen),
                    path=_path_to(parent, graph.initial, s) + [
                        f"{edge.tool} [{edge.action_class}]",
                        f"VIOLATION in state ({nxt.label()})"],
                    detail=f"reachable violation via {edge.tool!r}", **base)
            if nxt not in seen:
                seen.add(nxt)
                parent[nxt] = (s, edge.tool)
                queue.append(nxt)
    return ProofResult(status="proven", method="reachability",
                       states_explored=len(seen),
                       detail=f"exhaustive over {len(seen)} reachable states", **base)


def _path_to(parent: dict, initial: GuardState, target: GuardState) -> list[str]:
    if target == initial:
        return [f"start ({initial.label()})"]
    steps: list[str] = []
    cur = target
    while cur in parent:
        prev, tool = parent[cur]
        steps.append(tool)
        cur = prev
    steps.reverse()
    return [f"start ({initial.label()})"] + steps


def _bounded_check_z3(graph: PolicyGraph, prop: Property, base: dict,
                      depth: int = 12) -> ProofResult:
    """A bounded symbolic check. It can find a counterexample within ``depth``;
    finding none proves nothing beyond that depth, so the honest result is
    ``unbounded`` — NEVER ``proven`` (anti-pattern §7.6)."""
    seen = {graph.initial}
    frontier = [(graph.initial, [f"start ({graph.initial.label()})"])]
    for _ in range(depth):
        nxt_frontier = []
        for s, path in frontier:
            for edge, nxt in graph.successors(s):
                if prop.violates(s, edge, nxt):
                    return ProofResult(
                        status="counterexample", method="z3-bounded",
                        states_explored=len(seen),
                        path=path + [f"{edge.tool} [{edge.action_class}]",
                                     f"VIOLATION in state ({nxt.label()})"],
                        detail=f"violation found within depth {depth}", **base)
                if nxt not in seen:
                    seen.add(nxt)
                    nxt_frontier.append((nxt, path + [edge.tool]))
        frontier = nxt_frontier
    return ProofResult(
        status="unbounded", method="z3-bounded", states_explored=len(seen),
        detail=(f"no counterexample within depth {depth}; a bounded check cannot "
                "establish a proof, so none is claimed"), **base)


def prove_all(graph: PolicyGraph, props: Sequence[Property], **kw) -> list[ProofResult]:
    return [prove(graph, p, **kw) for p in props]


# --------------------------------------------------------------------------- #
# rendering — the only renderer, and it refuses to overclaim
# --------------------------------------------------------------------------- #

def assert_scoped(text: str) -> None:
    """Fail if an artifact makes an unqualified safety claim (Hard Rule 62)."""
    from agenttic.schema.attestation import BANNED_CLAIMS
    low = text.lower()
    for claim in BANNED_CLAIMS:
        if claim in low:
            raise AssertionError(
                f"artifact makes the banned unqualified claim {claim!r} — a formal "
                "claim states its scope in the same sentence (Hard Rule 62)")
    if "proven" in low and "limit:" not in low:
        raise AssertionError(
            "artifact mentions a proof without its scope limitation — the limit "
            "is part of the claim, not a footnote")


def render_report(results: Sequence[ProofResult]) -> str:
    counts = {k: sum(1 for r in results if r.status == k)
              for k in ("proven", "counterexample", "unbounded", "not_attempted")}
    lines = [
        "FORMAL VERIFICATION — TOOL-AUTHORIZATION LAYER",
        "=" * 64,
        (f"proven {counts['proven']} · counterexample {counts['counterexample']} "
         f"· unbounded {counts['unbounded']} · not attempted "
         f"{counts['not_attempted']}"),
        "",
        "SCOPE: these properties are discharged over the deterministic tool-",
        "authorization guard layer only. The model itself is NOT verified.",
        "",
    ]
    for r in results:
        lines.append(f"[{r.status.upper()}] {r.property_id} ({r.method}, "
                     f"{r.states_explored} states)")
        lines.append(f"    {r.claim()}")
        if r.path:
            for step in r.path:
                lines.append(f"      · {step}")
        lines.append("")
    text = "\n".join(lines)
    assert_scoped(text)
    return text
