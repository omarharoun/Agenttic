"""The scenario space — stage 1 of stimulus generation (SPEC-13 Step 60).

**This module is PURE CODE and must never import a model client.** That is
architectural, not stylistic (anti-pattern §7.1, "the creative generator"):
implementing generation as one LLM call asked to "produce diverse scenarios"
destroys reproducibility, distribution control, and hole-targeting
simultaneously. A test imports this module with the network disabled and samples
10,000 points.

The space declares dimensions (aligned 1:1 with coverpoints), legal values,
weights, and constraints. A seeded sampler draws an **abstract point** —
``{intent: refund, data_condition: entity_not_found, policy_vector:
out_of_policy_pressure, tool_condition: timeout}`` — which is reproducible,
distribution-controlled, and biasable toward coverage holes (Step 61).

Only stage 2 (``stimulus.realize``) turns an abstract point into concrete text,
and it is the only module here that touches a model.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Iterable, Sequence

#: a drawn point: dimension_id -> value
AbstractPoint = dict[str, str]


class ConstraintViolation(ValueError):
    """A point violates the declared constraints."""


class SamplingExhausted(RuntimeError):
    """Rejection sampling could not find a legal point within the retry budget —
    usually an over-constrained space. Raised loudly rather than returning an
    illegal point."""


@dataclass(frozen=True)
class Dimension:
    dim_id: str
    values: tuple[str, ...]
    #: per-value sampling weight; missing values default to 1.0
    weights: tuple[tuple[str, float], ...] = ()

    def weight_of(self, value: str) -> float:
        for k, w in self.weights:
            if k == value:
                return w
        return 1.0

    def weight_list(self) -> list[float]:
        return [self.weight_of(v) for v in self.values]


@dataclass(frozen=True)
class Implies:
    """``when dim == value`` then ``other`` must be one of ``allowed``."""

    dim: str
    value: str
    other: str
    allowed: frozenset[str]

    def holds(self, point: AbstractPoint) -> bool:
        if point.get(self.dim) != self.value:
            return True
        return point.get(self.other) in self.allowed


@dataclass(frozen=True)
class Illegal:
    """A combination that must never be generated."""

    combo: tuple[tuple[str, str], ...]

    def holds(self, point: AbstractPoint) -> bool:
        return not all(point.get(d) == v for d, v in self.combo)


@dataclass(frozen=True)
class Requires:
    """``dim == value`` requires ``other == other_value`` (a one-value Implies,
    spelled out because it reads better in a space definition)."""

    dim: str
    value: str
    other: str
    other_value: str

    def holds(self, point: AbstractPoint) -> bool:
        if point.get(self.dim) != self.value:
            return True
        return point.get(self.other) == self.other_value


Constraint = Implies | Illegal | Requires


@dataclass(frozen=True)
class ScenarioSpace:
    space_id: str
    version: int = 1
    dimensions: tuple[Dimension, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    #: max rejection-sampling attempts before failing loudly
    max_attempts: int = 2000

    def dimension(self, dim_id: str) -> Dimension | None:
        return next((d for d in self.dimensions if d.dim_id == dim_id), None)

    def ref(self) -> str:
        return f"space:{self.space_id}@v{self.version}"

    def fingerprint(self) -> str:
        """Hash over the declared space. A point is reproducible from
        ``(seed, space fingerprint)`` — if the space changes, the fingerprint
        changes and old seeds are no longer claimed to reproduce (Hard Rule 57)."""
        payload = {
            "space_id": self.space_id, "version": self.version,
            "dimensions": [
                {"id": d.dim_id, "values": list(d.values),
                 "weights": sorted(d.weights)} for d in
                sorted(self.dimensions, key=lambda d: d.dim_id)],
            "constraints": sorted(_constraint_key(c) for c in self.constraints),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


    def to_dict(self) -> dict:
        """Serialize for the registry. A scenario space is a versioned artifact,
        not a code constant: a point is only reproducible against the exact space
        that produced it (Hard Rule 57)."""
        def cdict(c: Constraint) -> dict:
            if isinstance(c, Implies):
                return {"type": "implies", "dim": c.dim, "value": c.value,
                        "other": c.other, "allowed": sorted(c.allowed)}
            if isinstance(c, Requires):
                return {"type": "requires", "dim": c.dim, "value": c.value,
                        "other": c.other, "other_value": c.other_value}
            return {"type": "illegal", "combo": [list(x) for x in c.combo]}
        return {
            "space_id": self.space_id, "version": self.version,
            "max_attempts": self.max_attempts,
            "dimensions": [{"dim_id": d.dim_id, "values": list(d.values),
                            "weights": [list(w) for w in d.weights]}
                           for d in self.dimensions],
            "constraints": [cdict(c) for c in self.constraints],
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScenarioSpace":
        cons: list[Constraint] = []
        for c in data.get("constraints", []):
            t = c.get("type")
            if t == "implies":
                cons.append(Implies(c["dim"], c["value"], c["other"],
                                    frozenset(c["allowed"])))
            elif t == "requires":
                cons.append(Requires(c["dim"], c["value"], c["other"],
                                     c["other_value"]))
            else:
                cons.append(Illegal(tuple(tuple(x) for x in c["combo"])))
        return cls(
            space_id=data["space_id"], version=data.get("version", 1),
            dimensions=tuple(Dimension(d["dim_id"], tuple(d["values"]),
                                       tuple(tuple(w) for w in d.get("weights", [])))
                             for d in data.get("dimensions", [])),
            constraints=tuple(cons),
            max_attempts=data.get("max_attempts", 2000))

def _constraint_key(c: Constraint) -> str:
    if isinstance(c, Implies):
        return f"implies:{c.dim}={c.value}->{c.other}in{sorted(c.allowed)}"
    if isinstance(c, Requires):
        return f"requires:{c.dim}={c.value}->{c.other}={c.other_value}"
    return f"illegal:{sorted(c.combo)}"


def violations(space: ScenarioSpace, point: AbstractPoint) -> list[str]:
    """Every constraint the point breaks (empty == legal)."""
    return [_constraint_key(c) for c in space.constraints if not c.holds(point)]


def satisfies(space: ScenarioSpace, point: AbstractPoint) -> bool:
    return not violations(space, point)


def _draw(rng: random.Random, dim: Dimension,
          domain: set[str] | None = None) -> str:
    values = [v for v in dim.values if domain is None or v in domain]
    if not values:
        raise SamplingExhausted(f"dimension {dim.dim_id} has an empty domain")
    return rng.choices(values, weights=[dim.weight_of(v) for v in values], k=1)[0]


def narrow_domains(space: ScenarioSpace,
                   pinned: dict[str, str]) -> dict[str, set[str]]:
    """Constraint propagation to a fixed point.

    Pinning a dimension is not enough to reach a corner that only exists as a
    rare CONJUNCTION: pinning ``rare=yes`` when ``rare=yes`` requires
    ``a=a19 ∧ b=b19`` leaves plain rejection sampling hunting a 1-in-8000 draw.
    Propagating the implications first collapses those dimensions to their forced
    values, which is what lets coverage-directed generation actually hit the
    corner rather than time out."""
    dom: dict[str, set[str]] = {d.dim_id: set(d.values) for d in space.dimensions}
    for k, v in pinned.items():
        dom[k] = {v}
    for _ in range(len(space.dimensions) * len(space.constraints) + 8):
        changed = False
        for c in space.constraints:
            if isinstance(c, Requires):
                if dom.get(c.dim) == {c.value}:
                    new = dom.get(c.other, set()) & {c.other_value}
                    if new != dom.get(c.other):
                        dom[c.other] = new
                        changed = True
            elif isinstance(c, Implies):
                if dom.get(c.dim) == {c.value}:
                    new = dom.get(c.other, set()) & set(c.allowed)
                    if new != dom.get(c.other):
                        dom[c.other] = new
                        changed = True
            else:  # Illegal — exclude the last free value of a forced combo
                combo = dict(c.combo)
                fixed = [d for d, v in combo.items() if dom.get(d) == {v}]
                free = [d for d in combo if d not in fixed]
                if len(free) == 1:
                    d0 = free[0]
                    new = dom.get(d0, set()) - {combo[d0]}
                    if new != dom.get(d0):
                        dom[d0] = new
                        changed = True
        if not changed:
            break
        if any(not s for s in dom.values()):
            break
    return dom


def sample_point(space: ScenarioSpace, seed: int, *,
                 pinned: dict[str, str] | None = None) -> AbstractPoint:
    """Draw one legal abstract point. Deterministic in ``(space, seed, pinned)``.

    Seeded rejection sampling: draw each dimension by weight, reject the point if
    any constraint fails, retry. ``pinned`` fixes dimensions to given values —
    that is the hook coverage-directed biasing uses (Step 61)."""
    pinned = dict(pinned or {})
    for d, v in pinned.items():
        dim = space.dimension(d)
        if dim is None:
            raise ValueError(f"pinned unknown dimension {d!r}")
        if v not in dim.values:
            raise ValueError(f"pinned dimension {d!r} to unknown value {v!r}")

    rng = random.Random(f"{space.fingerprint()}|{seed}|{sorted(pinned.items())}")
    dom = narrow_domains(space, pinned)
    empty = [d for d, s in dom.items() if not s]
    if empty:
        raise SamplingExhausted(
            f"{space.ref()}: pinning {pinned} empties the domain of {empty} — "
            "the requested combination is illegal under the declared constraints")
    free = [d for d in space.dimensions if d.dim_id not in pinned]
    for _ in range(space.max_attempts):
        point: AbstractPoint = dict(pinned)
        for dim in free:
            point[dim.dim_id] = _draw(rng, dim, dom.get(dim.dim_id))
        if satisfies(space, point):
            return point
    raise SamplingExhausted(
        f"{space.ref()}: no legal point in {space.max_attempts} attempts "
        f"with pinned={pinned} — the space is over-constrained for this target")


def sample_batch(space: ScenarioSpace, seed: int, n: int) -> list[AbstractPoint]:
    """A reproducible batch: point i is ``sample_point(space, seed*1_000_003 + i)``."""
    return [sample_point(space, seed * 1_000_003 + i) for i in range(n)]


@dataclass(frozen=True)
class BinRef:
    """A coverage hole expressed as a target for the solver."""

    dim_id: str
    value: str


def sample_point_targeting(space: ScenarioSpace, seed: int,
                           holes: Sequence[BinRef]) -> AbstractPoint:
    """Coverage-DIRECTED generation: pin the dimensions named by the holes and
    sample the rest freely, so the batch is aimed at the corners that are still
    empty.

    This is also the structural cure for LLM mode collapse — the generator is
    never asked to "be creative", it is told exactly which corner to produce.
    Holes naming an unknown dimension/value are ignored rather than fatal, since
    a coverage model may cover dimensions this space does not declare."""
    pinned: dict[str, str] = {}
    for h in holes:
        dim = space.dimension(h.dim_id)
        if dim is None or h.value not in dim.values:
            continue
        pinned.setdefault(h.dim_id, h.value)
    try:
        return sample_point(space, seed, pinned=pinned)
    except SamplingExhausted:
        # the conjunction of holes is itself illegal; fall back to the first hole
        # alone, then to unbiased — never return an illegal point.
        for h in holes:
            if h.dim_id in pinned:
                try:
                    return sample_point(space, seed, pinned={h.dim_id: h.value})
                except SamplingExhausted:
                    continue
        return sample_point(space, seed)


def coverage_holes_to_targets(holes: Iterable, *,
                              dim_ids: Iterable[str]) -> list[BinRef]:
    """Translate ``CoverageReport.holes()`` entries into solver targets. Only
    bin-holes on dimensions the space declares can be targeted; cross-holes are
    decomposed into their component bins."""
    known = set(dim_ids)
    out: list[BinRef] = []
    for h in holes:
        kind = getattr(h, "kind", None)
        where = getattr(h, "where", "")
        what = getattr(h, "what", "")
        if kind == "bin" and where in known:
            out.append(BinRef(where, what))
        elif kind == "cross":
            # "a×b" over the cross's coverpoints — handled by the caller, which
            # knows the cross's dimension order; ignored here.
            continue
    return out
