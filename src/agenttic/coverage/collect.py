"""Coverage collection + hole analysis (SPEC-13 Step 59).

**Two numbers, never one** (anti-pattern §7.4 — coverage theater):

* **Stimulus coverage** — which bins were *requested* by the abstract point.
* **Trace coverage** — which bins the run actually *exhibited*.

Generating a ``tool_condition=timeout`` scenario where the timeout never fired is
a stimulus hit and a trace miss. **Closure is computed on trace coverage.**
Reporting only the stimulus number would let a generator claim credit for
corners it never actually reached.

Illegal-bin hits are FAILURES and never count toward closure. Unhit bins are
always listed (Hard Rule 61) — waiving one requires a reason recorded on the
model version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Callable, Sequence

from agenttic.coverage.extractors import run_predicate
from agenttic.coverage.model import OTHER_BIN, Bin, Classifier, CoverageModel
from agenttic.schema.trace import Trace

#: An injected classifier evaluator. M41 ships deterministic extraction only;
#: without one, classifier-backed bins are reported UNEVALUATED (never "missed"),
#: and their coverpoint renders PROVISIONAL.
ClassifyFn = Callable[[Classifier, Trace, dict | None], bool]


@dataclass
class Sample:
    """One observation: a run, optionally with the abstract point that asked for
    it. ``requested`` maps coverpoint_id -> bin_id (the stimulus side)."""

    trace: Trace
    scenario: dict | None = None
    requested: dict[str, str] | None = None


@dataclass
class BinCoverage:
    bin_id: str
    trace_hits: int = 0          # the run EXHIBITED it
    stimulus_hits: int = 0       # the point REQUESTED it
    illegal: bool = False
    waived: bool = False
    provisional: bool = False
    unevaluated: bool = False    # classifier-backed with no evaluator supplied

    @property
    def hit(self) -> bool:
        return self.trace_hits > 0


@dataclass
class CoverpointCoverage:
    coverpoint_id: str
    kind: str
    required: bool
    provisional: bool
    bins: dict[str, BinCoverage] = field(default_factory=dict)

    def countable(self) -> list[BinCoverage]:
        return [b for b in self.bins.values()
                if not b.illegal and not b.waived and b.bin_id != OTHER_BIN
                and not b.unevaluated]

    @property
    def trace_closure(self) -> float:
        c = self.countable()
        return (sum(1 for b in c if b.hit) / len(c)) if c else 0.0

    @property
    def stimulus_closure(self) -> float:
        c = self.countable()
        return (sum(1 for b in c if b.stimulus_hits > 0) / len(c)) if c else 0.0

    @property
    def unhit(self) -> list[str]:
        return sorted(b.bin_id for b in self.countable() if not b.hit)

    @property
    def other_hits(self) -> int:
        b = self.bins.get(OTHER_BIN)
        return b.trace_hits if b else 0

    @property
    def illegal_hits(self) -> int:
        return sum(b.trace_hits for b in self.bins.values() if b.illegal)


@dataclass
class CrossCoverage:
    cross_id: str
    coverpoints: list[str]
    target_combos: list[tuple[str, ...]] = field(default_factory=list)
    hit_combos: set[tuple[str, ...]] = field(default_factory=set)

    @property
    def closure(self) -> float:
        if not self.target_combos:
            return 0.0
        return len(set(self.target_combos) & self.hit_combos) / len(self.target_combos)

    @property
    def unhit(self) -> list[tuple[str, ...]]:
        return sorted(set(self.target_combos) - self.hit_combos)


@dataclass
class Hole:
    """An unexercised corner. ``unreachable`` is decided by a human waiver; at
    collection time every hole is merely *unexercised*."""

    kind: str                       # "bin" | "cross"
    where: str                      # coverpoint_id or cross_id
    what: str                       # bin_id or "a×b" combination
    required: bool = True
    rank: float = 0.0               # higher = more important to fill


@dataclass
class IllegalHit:
    coverpoint_id: str
    bin_id: str
    count: int


@dataclass
class CoverageReport:
    model_ref: str
    bins_fingerprint: str
    n_samples: int
    coverpoints: dict[str, CoverpointCoverage] = field(default_factory=dict)
    crosses: dict[str, CrossCoverage] = field(default_factory=dict)
    illegal_hits: list[IllegalHit] = field(default_factory=list)
    closure_target: float = 0.95

    # -- the headline ------------------------------------------------------
    @property
    def trace_closure(self) -> float:
        """THE number. Computed on what runs exhibited, never on what was asked."""
        cps = [c for c in self.coverpoints.values() if c.required]
        vals = [c.trace_closure for c in cps] + [x.closure for x in self.crosses.values()]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def stimulus_closure(self) -> float:
        cps = [c for c in self.coverpoints.values() if c.required]
        return sum(c.stimulus_closure for c in cps) / len(cps) if cps else 0.0

    @property
    def closed(self) -> bool:
        return self.trace_closure >= self.closure_target and not self.illegal_hits

    @property
    def provisional_coverpoints(self) -> list[str]:
        return sorted(c.coverpoint_id for c in self.coverpoints.values() if c.provisional)

    def divergence(self) -> list[dict]:
        """Bins the stimulus REQUESTED but the trace never EXHIBITED — the
        generator asked for a corner it never actually reached."""
        out = []
        for cp in self.coverpoints.values():
            for b in cp.bins.values():
                if b.stimulus_hits > 0 and b.trace_hits == 0:
                    out.append({"coverpoint_id": cp.coverpoint_id, "bin_id": b.bin_id,
                                "requested": b.stimulus_hits, "exhibited": 0})
        return sorted(out, key=lambda d: (d["coverpoint_id"], d["bin_id"]))

    def other_drift(self) -> dict[str, float]:
        """Share of samples landing in each `other` bin. A rising number is a
        finding: the model is missing a dimension."""
        if not self.n_samples:
            return {}
        return {cp.coverpoint_id: round(cp.other_hits / self.n_samples, 4)
                for cp in self.coverpoints.values() if cp.other_hits}

    def holes(self) -> list[Hole]:
        """Unhit bins and cross combinations, ranked. NOTE: at M41 the rank is
        structural (required coverpoints and crosses first); ranking by the
        severity of the criteria that would have applied arrives with the CDV
        loop, which is where criteria are in scope."""
        out: list[Hole] = []
        for cp in self.coverpoints.values():
            for b in cp.unhit:
                out.append(Hole("bin", cp.coverpoint_id, b, cp.required,
                                rank=2.0 if cp.required else 1.0))
        for x in self.crosses.values():
            for combo in x.unhit:
                out.append(Hole("cross", x.cross_id, "×".join(combo), True, rank=3.0))
        return sorted(out, key=lambda h: (-h.rank, h.where, h.what))

    def as_dict(self) -> dict:
        return {
            "model": self.model_ref,
            "bins_fingerprint": self.bins_fingerprint,
            "samples": self.n_samples,
            "trace_closure": round(self.trace_closure, 4),
            "stimulus_closure": round(self.stimulus_closure, 4),
            "closure_target": self.closure_target,
            "closed": self.closed,
            "illegal_hits": [{"coverpoint_id": i.coverpoint_id, "bin_id": i.bin_id,
                              "count": i.count} for i in self.illegal_hits],
            "provisional_coverpoints": self.provisional_coverpoints,
            "other_drift": self.other_drift(),
            "stimulus_vs_trace_divergence": self.divergence(),
            "coverpoints": {
                cp.coverpoint_id: {
                    "trace_closure": round(cp.trace_closure, 4),
                    "stimulus_closure": round(cp.stimulus_closure, 4),
                    "unhit": cp.unhit, "other_hits": cp.other_hits,
                    "provisional": cp.provisional,
                } for cp in self.coverpoints.values()},
            "crosses": {x.cross_id: {"closure": round(x.closure, 4),
                                     "unhit": ["×".join(c) for c in x.unhit]}
                        for x in self.crosses.values()},
            "holes": [{"kind": h.kind, "where": h.where, "what": h.what}
                      for h in self.holes()],
        }

    def headline(self) -> str:
        prov = (f"  [PROVISIONAL: {', '.join(self.provisional_coverpoints)}]"
                if self.provisional_coverpoints else "")
        return (f"closure {self.trace_closure:.0%} of target {self.closure_target:.0%} "
                f"over {self.n_samples} samples "
                f"(stimulus {self.stimulus_closure:.0%}) — "
                f"{len(self.holes())} hole(s), {len(self.illegal_hits)} illegal hit(s)"
                + prov)


# --------------------------------------------------------------------------- #
# collection
# --------------------------------------------------------------------------- #

def _bin_matches(b: Bin, trace: Trace, scenario: dict | None,
                 classify: ClassifyFn | None) -> bool | None:
    """True/False, or None when the bin cannot be evaluated (classifier-backed
    with no evaluator supplied)."""
    if b.predicate_ref:
        return run_predicate(b.predicate_ref, trace, scenario)
    if b.classifier is not None:
        if classify is None:
            return None
        return bool(classify(b.classifier, trace, scenario))
    return False        # the `other` bin is resolved by the caller


def collect(model: CoverageModel, samples: Sequence[Sample], *,
            classify: ClassifyFn | None = None) -> CoverageReport:
    """Extract coverage from samples. Deterministic bins need no model calls;
    classifier bins are only evaluated if an evaluator is injected."""
    model.validate_against_registry()
    report = CoverageReport(model_ref=model.ref(),
                            bins_fingerprint=model.bins_fingerprint(),
                            n_samples=len(samples),
                            closure_target=model.closure_target)

    for cp in model.coverpoints:
        cov = CoverpointCoverage(cp.coverpoint_id, cp.kind, cp.required, cp.provisional)
        for b in cp.bins:
            cov.bins[b.bin_id] = BinCoverage(
                bin_id=b.bin_id, illegal=b.illegal, waived=b.waived,
                provisional=b.provisional,
                unevaluated=(b.classifier is not None and classify is None))
        report.coverpoints[cp.coverpoint_id] = cov

    # per-sample extraction
    per_sample_hits: list[dict[str, set[str]]] = []
    for sample in samples:
        hits: dict[str, set[str]] = {}
        for cp in model.coverpoints:
            cov = report.coverpoints[cp.coverpoint_id]
            matched: set[str] = set()
            for b in cp.bins:
                if b.bin_id == OTHER_BIN:
                    continue
                m = _bin_matches(b, sample.trace, sample.scenario, classify)
                if m:
                    matched.add(b.bin_id)
                    cov.bins[b.bin_id].trace_hits += 1
            if not matched:
                # exhaustive by construction: nothing matched -> `other`
                cov.bins[OTHER_BIN].trace_hits += 1
                matched.add(OTHER_BIN)
            hits[cp.coverpoint_id] = matched

            # the stimulus side, recorded separately and never mixed in
            want = (sample.requested or {}).get(cp.coverpoint_id)
            if want and want in cov.bins:
                cov.bins[want].stimulus_hits += 1
        per_sample_hits.append(hits)

    # illegal bins are failures, excluded from closure by countable()
    for cp_id, cov in report.coverpoints.items():
        for b in cov.bins.values():
            if b.illegal and b.trace_hits:
                report.illegal_hits.append(IllegalHit(cp_id, b.bin_id, b.trace_hits))

    # crosses
    for x in model.crosses:
        cc = CrossCoverage(x.cross_id, list(x.coverpoints))
        cps = [model.coverpoint(c) for c in x.coverpoints]
        legal_axes = [[b.bin_id for b in cp.countable_bins()] for cp in cps]  # type: ignore[union-attr]
        illegal = {tuple(combo[c] for c in x.coverpoints)
                   for combo in x.illegal_combinations
                   if all(c in combo for c in x.coverpoints)}
        if x.target == "all":
            cc.target_combos = [c for c in product(*legal_axes) if c not in illegal]
        else:
            cc.target_combos = [tuple(t[c] for c in x.coverpoints)
                                for t in x.target  # type: ignore[union-attr]
                                if tuple(t[c] for c in x.coverpoints) not in illegal]
        for hits in per_sample_hits:
            axes = [sorted(hits.get(c, set())) for c in x.coverpoints]
            for combo in product(*axes):
                if combo in illegal:
                    continue
                cc.hit_combos.add(combo)
        report.crosses[x.cross_id] = cc

    return report
