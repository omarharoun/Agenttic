"""The CDV loop — generate → run → find holes → bias → repeat (SPEC-13 Step 61).

The loop that closes coverage instead of counting passes:

1. Generate a seeded batch from the scenario space.
2. Run it through the harness (injected, so this module stays testable offline).
3. Extract coverage from scenario + trace; score with the existing engine.
4. **Analyze holes** — unhit bins and unhit cross combinations, ranked.
5. **Bias the next batch toward the holes** by pinning the solver at them. This
   is coverage-*directed* generation, and it is also the structural cure for LLM
   mode collapse: the generator is never asked to "be creative", it is told
   exactly which corner to produce.
6. Repeat until the closure target or the budget is exhausted.

Two things hardware does that agent evaluation does not:

* **The bug-discovery curve** over distinct failure *signatures*
  ``(criterion_id, failure_mode, trajectory_bin)``. A flattening curve is the
  convergence signal used to decide you have looked hard enough; a still-rising
  curve means keep running.
* **Failures become permanent tests.** Every failing generated scenario is frozen
  (seed + realized text + derived expectation) and *proposed* into the directed
  regression suite through the normal human gate — never auto-added
  (Hard Rule 63).

The budget is hard: it stops the loop cleanly and reports partial closure with
closure-per-dollar. It never silently truncates the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agenttic.coverage.collect import CoverageReport, Sample, collect
from agenttic.coverage.model import CoverageModel
from agenttic.schema.trace import Trace
from agenttic.stimulus.oracle import PolicyDoc
from agenttic.stimulus.realize import RealizedScenario, realize
from agenttic.stimulus.space import (
    BinRef, ScenarioSpace, sample_point, sample_point_targeting)


@dataclass(frozen=True)
class Budget:
    """A hard ceiling. Reaching it stops the loop cleanly with partial closure."""

    max_scenarios: int = 200
    max_dollars: float = 25.0
    max_rounds: int = 12


@dataclass(frozen=True)
class FailureSignature:
    """What makes two failures 'the same bug' for convergence purposes."""

    criterion_id: str
    failure_mode: str
    trajectory_bin: str = ""

    def key(self) -> str:
        return f"{self.criterion_id}|{self.failure_mode}|{self.trajectory_bin}"


@dataclass
class ExecutionResult:
    """What the injected executor returns for one scenario."""

    trace: Trace
    passed: bool = True
    failures: list[FailureSignature] = field(default_factory=list)
    cost_usd: float = 0.0


#: execute(scenario) -> ExecutionResult. Real wiring runs the existing harness +
#: scoring engine; tests inject a deterministic stand-in.
Executor = Callable[[RealizedScenario], ExecutionResult]


@dataclass
class FrozenRegression:
    """A failing generated scenario, frozen for replay. PROPOSED only — promotion
    into the directed suite goes through the existing human gate."""

    scenario: dict
    seed: int
    signature: str
    approved: bool = False       # Hard Rule 63 / no silent suite growth

    @property
    def scenario_id(self) -> str:
        return str(self.scenario.get("scenario_id", ""))


@dataclass
class RoundResult:
    index: int
    scenarios: int
    biased: bool
    closure: float
    new_signatures: int
    targeted: list[str] = field(default_factory=list)


@dataclass
class CDVResult:
    report: CoverageReport
    rounds: list[RoundResult] = field(default_factory=list)
    scenarios_run: int = 0
    dollars_spent: float = 0.0
    stopped_because: str = ""
    bug_curve: list[tuple[int, int]] = field(default_factory=list)
    frozen_regressions: list[FrozenRegression] = field(default_factory=list)
    scenarios: list[RealizedScenario] = field(default_factory=list)

    @property
    def closure(self) -> float:
        return self.report.trace_closure

    @property
    def closed(self) -> bool:
        return self.report.closed

    @property
    def closure_per_dollar(self) -> float:
        return (self.closure / self.dollars_spent) if self.dollars_spent else 0.0

    @property
    def distinct_signatures(self) -> int:
        return self.bug_curve[-1][1] if self.bug_curve else 0

    def scenarios_since_last_new_signature(self) -> int:
        """The convergence read: how long since anything genuinely new appeared."""
        if not self.bug_curve:
            return 0
        last = self.bug_curve[-1][1]
        for n, count in reversed(self.bug_curve):
            if count < last:
                return self.scenarios_run - n
        return self.scenarios_run

    def curve_flattened(self, *, window: int = 40) -> bool:
        return self.scenarios_since_last_new_signature() >= window

    def as_dict(self) -> dict:
        return {
            "closure": round(self.closure, 4),
            "closed": self.closed,
            "closure_target": self.report.closure_target,
            "scenarios_run": self.scenarios_run,
            "dollars_spent": round(self.dollars_spent, 4),
            "closure_per_dollar": round(self.closure_per_dollar, 4),
            "stopped_because": self.stopped_because,
            "rounds": [{"index": r.index, "scenarios": r.scenarios,
                        "biased": r.biased, "closure": round(r.closure, 4),
                        "new_signatures": r.new_signatures,
                        "targeted": r.targeted} for r in self.rounds],
            "bug_curve": self.bug_curve,
            "distinct_failure_signatures": self.distinct_signatures,
            "scenarios_since_last_new_signature":
                self.scenarios_since_last_new_signature(),
            "curve_flattened": self.curve_flattened(),
            "holes_remaining": [{"kind": h.kind, "where": h.where, "what": h.what}
                                for h in self.report.holes()],
            "frozen_regressions": len(self.frozen_regressions),
            "coverage": self.report.as_dict(),
        }


# --------------------------------------------------------------------------- #
# hole -> solver target
# --------------------------------------------------------------------------- #

def holes_to_targets(report: CoverageReport, model: CoverageModel,
                     space: ScenarioSpace) -> list[list[BinRef]]:
    """Translate ranked coverage holes into solver targets, one target-set per
    scenario. Cross holes are decomposed into their component bins — that is how
    the loop reaches a corner that only exists as a conjunction."""
    dims = {d.dim_id for d in space.dimensions}
    out: list[list[BinRef]] = []
    for h in report.holes():
        if h.kind == "bin":
            if h.where in dims:
                out.append([BinRef(h.where, h.what)])
        else:
            cross = next((x for x in model.crosses if x.cross_id == h.where), None)
            if cross is None:
                continue
            values = h.what.split("×")
            refs = [BinRef(cp, v) for cp, v in zip(cross.coverpoints, values)
                    if cp in dims]
            if refs:
                out.append(refs)
    return out


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #

def run_until_closure(
    space: ScenarioSpace,
    coverage_model: CoverageModel,
    execute: Executor,
    budget: Budget = Budget(),
    *,
    seed: int = 0,
    batch_size: int = 10,
    policy: PolicyDoc | None = None,
    realize_client=None,
    classify=None,
    bias: bool = True,
) -> CDVResult:
    """Run the CDV loop until closure or budget. ``bias=False`` runs plain
    unbiased random — the control arm that proves direction works."""
    policy = policy or PolicyDoc()
    samples: list[Sample] = []
    scenarios: list[RealizedScenario] = []
    result = CDVResult(report=collect(coverage_model, [], classify=classify))
    seen_signatures: set[str] = set()
    draw = 0

    for rnd in range(budget.max_rounds):
        remaining = budget.max_scenarios - result.scenarios_run
        if remaining <= 0:
            result.stopped_because = "scenario budget exhausted"
            break
        if result.dollars_spent >= budget.max_dollars:
            result.stopped_because = "dollar budget exhausted"
            break
        n = min(batch_size, remaining)

        # --- 1. generate -------------------------------------------------
        targets: list[list[BinRef]] = []
        if bias and rnd > 0:
            targets = holes_to_targets(result.report, coverage_model, space)
        points = []
        targeted_labels: list[str] = []
        for i in range(n):
            draw += 1
            s = seed * 1_000_003 + draw
            if targets:
                tgt = targets[i % len(targets)]
                points.append(sample_point_targeting(space, s, tgt))
                targeted_labels.append(
                    ",".join(f"{t.dim_id}={t.value}" for t in tgt))
            else:
                points.append(sample_point(space, s))

        # --- 2. realize + run --------------------------------------------
        new_sigs = 0
        for p, i in zip(points, range(n)):
            scn = realize(p, seed * 1_000_003 + draw - (n - 1 - i), space,
                          policy=policy, client=realize_client)
            ex = execute(scn)
            scenarios.append(scn)
            samples.append(Sample(trace=ex.trace, scenario=scn.as_dict(),
                                  requested=dict(p)))
            result.scenarios_run += 1
            result.dollars_spent += ex.cost_usd

            for sig in ex.failures:
                if sig.key() not in seen_signatures:
                    seen_signatures.add(sig.key())
                    new_sigs += 1
            if not ex.passed:
                # failures become permanent tests — proposed, never auto-added
                result.frozen_regressions.append(FrozenRegression(
                    scenario=scn.as_dict(), seed=scn.seed,
                    signature=(ex.failures[0].key() if ex.failures else "unknown")))
            result.bug_curve.append((result.scenarios_run, len(seen_signatures)))

            if result.dollars_spent >= budget.max_dollars:
                break

        # --- 3. extract coverage ------------------------------------------
        result.report = collect(coverage_model, samples, classify=classify)
        result.rounds.append(RoundResult(
            index=rnd, scenarios=n, biased=bool(targets),
            closure=result.report.trace_closure, new_signatures=new_sigs,
            targeted=sorted(set(targeted_labels))[:6]))

        if result.report.closed:
            result.stopped_because = "closure target reached"
            break
    else:
        result.stopped_because = result.stopped_because or "round limit reached"

    if not result.stopped_because:
        result.stopped_because = (
            "scenario budget exhausted" if result.scenarios_run >= budget.max_scenarios
            else "dollar budget exhausted")
    result.scenarios = scenarios
    return result


def replay(frozen: FrozenRegression, space: ScenarioSpace,
           policy: PolicyDoc | None = None) -> RealizedScenario:
    """Reproduce a frozen scenario exactly. The stored text is authoritative —
    replay never re-generates and hopes for the same words (Hard Rule 57)."""
    stored = frozen.scenario
    if stored.get("space_fingerprint") != space.fingerprint():
        raise ValueError(
            f"scenario {stored.get('scenario_id')} was generated against space "
            f"fingerprint {stored.get('space_fingerprint')} but the current space "
            f"is {space.fingerprint()} — the space changed, so this seed no longer "
            "reproduces it. Replay from the stored text instead.")
    scn = realize(stored["point"], frozen.seed, space, policy=policy)
    if scn.text != stored["text"]:                 # template drift guard
        scn.text = stored["text"]
        scn.realized_by = "replayed-verbatim"
    return scn
