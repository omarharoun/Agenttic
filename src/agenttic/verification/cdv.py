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

from agenttic.coverage.collect import CoverageReport, Hole, Sample, collect
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
    #: Holes remaining at the end that the solver COULD NOT AIM AT, each with the
    #: reason. Computed in both arms — being un-steerable is a property of the
    #: (report, model, space) triple, not of whether biasing was switched on —
    #: so a control run discloses the same limitation the biased one does.
    unaimable_holes: list[UnaimableHole] = field(default_factory=list)

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
            # Holes with NO steerable axis at all. Printed next to
            # `holes_remaining` on purpose: a hole the suite has not got to yet
            # and a hole the loop has given up on read identically in a count,
            # and only the second is a finding against the instrumentation rather
            # than against the suite. A lower bound, not the whole gap — see
            # `UnaimableHole` for why a partly-steerable cross is not in here.
            "unaimable_holes": [u.as_dict() for u in self.unaimable_holes],
            "frozen_regressions": len(self.frozen_regressions),
            "coverage": self.report.as_dict(),
        }


# --------------------------------------------------------------------------- #
# hole -> solver target
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class UnaimableHole:
    """A hole the solver cannot steer at, carrying WHY it cannot.

    The previous implementation ``continue``d past every hole whose coverpoint is
    not a stimulus dimension. Under ``baseline_model`` v3 against any space
    derived from an agent surface that is four of the six coverpoints —
    ``trajectory``, ``agent_steps``, ``action_risk`` are run OUTPUTS and
    ``session_shape`` is not measurable — so the loop was silently deciding it
    would never aim at most of its own hole list while the report next to it said
    "N holes remaining". A gap the loop has given up on is exactly the kind of
    fact this product exists to print, so it is returned on
    ``CDVResult.as_dict()['unaimable_holes']`` and rendered by ``agenttic cdv``.

    Two things this list is NOT, both of which an earlier version of this
    docstring got wrong:

    * It is not "every hole no batch will ever reach". The split computed here is
      *zero* steerable axes versus *at least one* — so a CROSS hole with one
      steerable component is filed as aimable even when its other component is an
      output coverpoint the loop can never drive. Measured on
      ``baseline_model`` × ``seed_space`` after one clean run: 53 of 75 holes
      filed aimable while containing an unsteerable component, against 13
      disclosed here. Aiming at the steerable half is still worth doing — that is
      how the loop reaches a corner that only exists as a conjunction — but the
      cell is not thereby reachable, and this list does not claim it is.
    * It is therefore a LOWER BOUND on what the loop cannot close, and reading it
      as the complete account would understate the gap.
    """

    kind: str
    where: str
    what: str
    reason: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "where": self.where, "what": self.what,
                "reason": self.reason}


@dataclass(frozen=True)
class HoleTargets:
    """What :func:`holes_to_targets` produced: what it can aim at, and what it
    cannot. Both halves are the answer; only returning the first half is what
    made the aiming un-diagnosable for two phases."""

    targets: list[list[BinRef]]
    unaimable: list[UnaimableHole] = field(default_factory=list)


def _exhibited(report: CoverageReport, coverpoint_id: str, bin_id: str) -> bool:
    """Did any run in this report EXHIBIT that bin? ``BinCoverage.hit`` — trace
    hits, never stimulus hits: a bin the generator asked for and the run never
    produced is still a hole, and aiming at it again is the correct move."""
    cp = report.coverpoints.get(coverpoint_id)
    b = cp.bins.get(bin_id) if cp is not None else None
    return bool(b is not None and b.hit)


def _drawn(report: CoverageReport, key: tuple[tuple[str, str], ...]) -> int:
    """How many runs already exhibited the bins this target would pin."""
    return sum(report.coverpoints[cp].bins[v].trace_hits for cp, v in key
               if cp in report.coverpoints and v in report.coverpoints[cp].bins)


def _aim_at(hole: Hole, report: CoverageReport, model: CoverageModel,
            space: ScenarioSpace) -> tuple[list[BinRef] | None, str]:
    """One hole -> a solver target-set, or ``(None, why it cannot be aimed at)``."""
    dims = {d.dim_id: d for d in space.dimensions}

    if hole.kind == "bin":
        dim = dims.get(hole.where)
        if dim is None:
            return None, (f"{hole.where} is not a dimension of {space.ref()} — "
                          "it is an output of the run, not a knob the solver can "
                          "turn")
        if hole.what not in dim.values:
            return None, (f"{space.ref()} declares no value {hole.what!r} for "
                          f"{hole.where}")
        if _exhibited(report, hole.where, hole.what):
            # Guard on the entry point rather than a live branch: `holes()`
            # derives bin holes from `cp.unhit`, so it cannot hand us a hit bin
            # today. It CAN be handed a report built by hand (``_scored()`` says
            # as much), and a target that pins a bin the suite already produces
            # is the exact waste this whole function was rewritten to remove.
            return None, f"{hole.where}={hole.what} has already been exhibited"
        return [BinRef(hole.where, hole.what)], ""

    cross = next((x for x in model.crosses if x.cross_id == hole.where), None)
    if cross is None:
        return None, f"{model.ref()} declares no cross {hole.where!r}"
    pairs = list(zip(cross.coverpoints, hole.what.split("×")))
    steerable = [(cp, v) for cp, v in pairs
                 if cp in dims and v in dims[cp].values]
    if not steerable:
        return None, (f"none of {', '.join(cp for cp, _ in pairs)} is a "
                      f"dimension of {space.ref()} — this cell is only reachable "
                      "by luck, whatever the batch asks for")
    return [BinRef(cp, v) for cp, v in steerable], ""


def holes_to_targets(report: CoverageReport, model: CoverageModel,
                     space: ScenarioSpace) -> HoleTargets:
    """Translate coverage holes into solver targets — deduplicated, ranked by how
    much each target still buys, and with the un-aimable remainder disclosed.

    Measured against the control arm on seeds 5/11/23, 60 scenarios, 6 rounds,
    ``--surface support``, scripted agent — the run in the ``ops.cdv_op``
    write-up, where aiming LOST to unbiased random on every seed:

    ==========  ========  ==========  ========
    seed        biased    unbiased    delta
    ==========  ========  ==========  ========
    5           0.7451    0.7003      +0.0448
    11          0.7065    0.7420      -0.0355
    23          0.7543    0.7457      +0.0086
    ==========  ========  ==========  ========

    Seed 23's unbiased arm read 0.7148 when this was first measured, making the
    delta look like +0.0395. It moved because ``stale_data`` stopped being
    unreachable (``realize._FAULT_CALL_INDEX`` now stages it on the lookup that
    FOLLOWS a write, where a stale read is distinguishable from a fresh one), and
    the extra reachable bin went to the unbiased arm. Corrected here rather than
    left standing: a docstring figure that no longer reproduces on the tree it
    ships in is the defect this module spends its own paragraphs warning about,
    and this one overstated the win 4.6x.

    Three seeds is not evidence, so the same two arms over 21 seeds (1..20 plus
    23), unbiased mean 0.7279 throughout — MEASURED BEFORE that ``stale_data``
    fix and not re-run since, so read the block below as the state of the tree at
    the time the targeting changed, not as the current numbers:

    ==========================  ==============  ==========
    arm                         mean closure    seeds won
    ==========================  ==============  ==========
    biased, before this change  0.6783          3 / 21
    biased, after               0.7342          10 / 21
    ==========================  ==============  ==========

    **State the size of that honestly.** Aiming went from a reliable 5-point LOSS
    (-0.0495) to a 0.6-point win (+0.0063); it wins 10 seeds, ties 2 and still
    loses 9. What was broken is fixed — directing the loop no longer costs
    closure — but direction is not yet a decisive lever, and nothing in this
    module should be read as claiming it is. The remaining ceiling is not in the
    ranking: it is that ``tool_x_trajectory`` has 54 cells and one steerable
    axis, and that three bins in this pairing (``data_condition=ambiguous`` and
    ``contradictory``, ``tool_condition=stale_data``) were requested 14-24 times
    each and exhibited ZERO times, because nothing in the retail world emits
    them. ``report.divergence()`` already names all three. That is a producer
    gap, and no target ranking closes it.

    Three rules, each a measured defect in the version before it:

    * **Deduplicate the target-sets.** ``CoverageReport.holes()`` ranks every
      cross cell at 3.0 and breaks the tie alphabetically, so the head of the
      list was ``all_ok×budget_exceeded``, ``all_ok×direct_answer``, … — the
      seven-to-eight cells (however many of the nine ``trajectory`` bins are
      still open) that all reduce to the single pin ``tool_condition=all_ok``,
      because ``trajectory`` is not a dimension any space declares. A batch of 10
      took ``targets[i % len(targets)]``, so those cells filled it by themselves:
      39 of 60 scenarios went to ``all_ok`` (a bin already exhibited 36 times)
      and ``rate_limited`` was requested ZERO times by a loop whose whole job was
      to request it. They are one target, not seven.
    * **Rank by what a target still buys, not alphabetically.** A target-set's
      weight is the summed rank of every hole it serves, DIVIDED BY how many runs
      already exhibited the bins it pins. That is the covered-bin rule in the form
      that measured best: ``all_ok`` with 36 trace hits sinks to the bottom of the
      list instead of the top, while a rare-but-needed value floats up. Hard-
      DROPPING every already-exhibited component instead was tried and measures
      WORSE — 21-seed mean 0.7217 against 0.7342, i.e. back under unbiased —
      because ``tool_x_trajectory`` has 54 cells and only one steerable axis, so
      refusing to re-pin a tool value once it has been seen once abandons the
      cross entirely. The demotion is the covered-bin rule in the form that
      survived measurement; the hard form is recorded here because it is the
      obvious thing to try next and it does not work.
    * **Never aim a BIN hole at a bin already exhibited** (``_aim_at``). Say
      plainly how far that goes: ``holes()`` derives bin holes from ``cp.unhit``,
      so on the live path it cannot hand this function a hit bin, and the check
      is a guard on a public entry point rather than a branch that fires every
      round. The covered-bin waste that was actually costing closure is removed
      by the two rules above, and it is those two the measurements move.

    ``holes()`` is deliberately NOT touched. It is the public "what is untested"
    surface for MCP and reporting, ordered for a human reader and pinned by its
    own tests; the solver ranking its own copy differently is not a disagreement
    about the facts.
    """
    weight: dict[tuple, float] = {}
    first: dict[tuple, list[BinRef]] = {}
    order: list[tuple] = []
    unaimable: list[UnaimableHole] = []

    for h in report.holes():
        refs, reason = _aim_at(h, report, model, space)
        if refs is None:
            unaimable.append(UnaimableHole(h.kind, h.where, h.what, reason))
            continue
        key = tuple(sorted((r.dim_id, r.value) for r in refs))
        if key not in first:
            first[key] = refs
            order.append(key)
        weight[key] = weight.get(key, 0.0) + h.rank

    order.sort(key=lambda k: (-weight[k] / (1.0 + _drawn(report, k)), k))
    return HoleTargets([first[k] for k in order], unaimable)


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
    #: Where the round-robin over the target list resumes. It advances ACROSS
    #: rounds rather than restarting at 0, because `targets[i % len(targets)]`
    #: over `range(batch_size)` only ever reaches the first `batch_size` entries:
    #: with 62 aimable targets and a batch of 10, targets 10..61 were never once
    #: requested no matter how many rounds ran.
    cursor = 0

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
            targets = holes_to_targets(result.report, coverage_model, space).targets
        points = []
        targeted_labels: list[str] = []
        for i in range(n):
            draw += 1
            s = seed * 1_000_003 + draw
            if targets:
                tgt = targets[(cursor + i) % len(targets)]
                points.append(sample_point_targeting(space, s, tgt))
                targeted_labels.append(
                    ",".join(f"{t.dim_id}={t.value}" for t in tgt))
            else:
                points.append(sample_point(space, s))
        cursor += n if targets else 0

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
    result.unaimable_holes = holes_to_targets(
        result.report, coverage_model, space).unaimable
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
