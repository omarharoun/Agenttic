"""The fit gate: discrimination (SPEC-9 Step 42) — the moat's proof.

A synthesized rubric is not shippable until it is shown to *discriminate*: a
rubric on which every agent scores the same measures nothing, however well
written. We assemble a **reference panel** of agents of known relative quality
for the archetype — a strong config, a deliberately weakened config, and the
null agent — run them through the draft rubric+suite at k ≥ 4, and require:

  * the rubric orders the panel correctly (strong > weak > null), and
  * the strong and null ends separate with **non-overlapping** Wilson intervals
    on pass^k.

A criterion on which the whole panel ties is flagged ``non-discriminating``
(dead weight — cut or fix) before human review. The gate is deny-by-default
(mirrors ``camp.gate.PromotionGate``): absence of a positive verdict is a fail.
Failing rubrics return to synthesis with the failing criteria named (Step 44).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agenttic.adapters.base import AgentAdapter
from agenttic.metrics.reliability import case_passes_k, pass_hat_k
from agenttic.rubric_engine.synthesize import DraftRubric
from agenttic.schema.rubric import Rubric
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import Span, Trace
from agenttic.scoring.engine import DEFAULT_PASS_THRESHOLD, score_run
from agenttic.stats import wilson_interval

MIN_K = 4                     # Step 42: run the panel at k >= 4
DEFAULT_TIE_EPS = 0.05        # per-criterion spread below this == the panel ties


# --------------------------------------------------------------------------- #
# The null agent (SPEC-6) — a real reference-panel member
# --------------------------------------------------------------------------- #

class NullAgent(AgentAdapter):
    """A baseline that does nothing useful: it returns a constant, uninformative
    answer and calls no tools. Non-empty (so it is scored as genuine task
    failures, not excluded as a non-result), and deterministic (so its pass^k is
    stable). Any rubric worth shipping must rank a real agent above this."""

    def __init__(self, agent_id: str = "null-agent",
                 answer: str = "I cannot help with that request."):
        self.agent_id = agent_id
        self.visibility = "glass_box"
        self._answer = answer
        self._n = 0

    def describe(self) -> dict:
        return {"agent": "null", "answer": self._answer}

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        from datetime import datetime, timezone
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._n += 1
        spans = [Span(span_id=f"n{self._n}", kind="final_output",
                      name="final_output", start_time=now, end_time=now)]
        return Trace(trace_id=f"{self.agent_id}-{test_case_id}-{self._n}",
                     agent_id=self.agent_id, agent_config_hash=self.config_hash(),
                     test_case_id=test_case_id, spans=spans,
                     visibility="glass_box", final_output=self._answer)


# --------------------------------------------------------------------------- #
# Panel + result data
# --------------------------------------------------------------------------- #

@dataclass
class PanelMember:
    """One reference agent of known relative quality. Higher ``quality_rank`` is
    a better agent (strong=2, weak=1, null=0 by convention; public-benchmark
    agents of known standing slot in by their standing)."""

    agent_id: str
    quality: str
    quality_rank: float
    adapter: AgentAdapter


@dataclass
class MemberResult:
    agent_id: str
    quality: str
    quality_rank: float
    n_cases: int
    k: int
    pass_hat_k: float
    wilson_low: float
    wilson_high: float
    per_criterion_mean: dict[str, float] = field(default_factory=dict)


@dataclass
class CriterionDiscrimination:
    criterion_id: str
    spread: float                       # max - min member mean
    discriminates: bool
    per_member: dict[str, float] = field(default_factory=dict)


@dataclass
class DiscriminationResult:
    members: list[MemberResult]          # observed order, best -> worst
    ranking_correct: bool
    ends_separated: bool
    strong_id: str
    null_id: str
    per_criterion: list[CriterionDiscrimination]
    non_discriminating: list[str]
    passes_gate: bool
    reason: str
    k: int


@dataclass
class GateDecision:
    """Deny-by-default (mirrors camp.gate.PromotionGate): no positive verdict
    means no."""

    approved: bool = False
    reasons: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Running the panel (real scoring — reuses the engine)
# --------------------------------------------------------------------------- #

def run_member(
    member: PanelMember,
    cases: list[TestCase],
    rubric: Rubric,
    *,
    k: int = MIN_K,
    judge=None,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
) -> MemberResult:
    """Run one panel member k times per case and reduce to a MemberResult."""
    from collections import defaultdict
    per_case: list[list[bool]] = []
    crit: dict[str, list[float]] = defaultdict(list)
    for tc in cases:
        runs: list[bool] = []
        for _ in range(k):
            trace = member.adapter.run(tc.input, test_case_id=tc.test_id)
            rs = score_run(trace, tc, rubric, judge, pass_threshold=pass_threshold)
            runs.append(rs.passed)
            for cs in rs.criterion_scores:
                crit[cs.criterion_id].append(cs.score)
        per_case.append(runs)
    n = len(per_case)
    successes = sum(1 for r in per_case if case_passes_k(r))
    low, high = wilson_interval(successes, n) if n else (0.0, 0.0)
    return MemberResult(
        agent_id=member.agent_id, quality=member.quality,
        quality_rank=member.quality_rank, n_cases=n, k=k,
        pass_hat_k=pass_hat_k(per_case),
        wilson_low=round(low, 4), wilson_high=round(high, 4),
        per_criterion_mean={cid: sum(v) / len(v) for cid, v in crit.items()},
    )


def run_reference_panel(
    panel: list[PanelMember],
    cases: list[TestCase],
    rubric: Rubric,
    *,
    k: int = MIN_K,
    judge=None,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
) -> list[MemberResult]:
    if k < MIN_K:
        raise ValueError(f"discrimination requires k >= {MIN_K}, got {k}")
    return [run_member(m, cases, rubric, k=k, judge=judge,
                       pass_threshold=pass_threshold) for m in panel]


def default_panel(strong: AgentAdapter, weak: AgentAdapter,
                  *, extra: list[PanelMember] | None = None) -> list[PanelMember]:
    """The canonical strong/weak/null panel, plus any public-benchmark members."""
    panel = [
        PanelMember(strong.agent_id, "strong", 2.0, strong),
        PanelMember(weak.agent_id, "weak", 1.0, weak),
        PanelMember("null-agent", "null", 0.0, NullAgent()),
    ]
    return panel + (extra or [])


# --------------------------------------------------------------------------- #
# The discrimination score + gate (pure math)
# --------------------------------------------------------------------------- #

def discriminate(
    members: list[MemberResult],
    rubric: Rubric,
    *,
    tie_eps: float = DEFAULT_TIE_EPS,
) -> DiscriminationResult:
    """Score whether ``rubric`` discriminates the panel. Pure function of the
    per-member results — no scoring, no LLM."""
    if len(members) < 2:
        raise ValueError("discrimination needs at least two panel members")
    observed = sorted(members, key=lambda m: m.pass_hat_k, reverse=True)

    # (1) ranking correct: every strictly-better-quality member must not score
    # below a lower-quality one (point estimate on pass^k).
    ranking_correct = True
    for a in members:
        for b in members:
            if a.quality_rank > b.quality_rank and a.pass_hat_k < b.pass_hat_k:
                ranking_correct = False

    strong = max(members, key=lambda m: m.quality_rank)
    null = min(members, key=lambda m: m.quality_rank)
    # (2) ends separated: strong's interval sits entirely above null's.
    ends_separated = strong.wilson_low > null.wilson_high

    # (3) per-criterion tie detection.
    per_criterion: list[CriterionDiscrimination] = []
    non_discriminating: list[str] = []
    for c in rubric.criteria:
        means = {m.agent_id: m.per_criterion_mean.get(c.criterion_id)
                 for m in members}
        present = [v for v in means.values() if v is not None]
        if len(present) < 2:
            spread = 0.0
        else:
            spread = max(present) - min(present)
        discriminates = spread >= tie_eps
        per_criterion.append(CriterionDiscrimination(
            c.criterion_id, round(spread, 4), discriminates,
            {k: (round(v, 4) if v is not None else None) for k, v in means.items()}))
        if not discriminates:
            non_discriminating.append(c.criterion_id)

    passes = ranking_correct and ends_separated
    reason = _reason(ranking_correct, ends_separated, strong, null,
                     non_discriminating, len(rubric.criteria))
    return DiscriminationResult(
        members=observed, ranking_correct=ranking_correct,
        ends_separated=ends_separated, strong_id=strong.agent_id,
        null_id=null.agent_id, per_criterion=per_criterion,
        non_discriminating=non_discriminating, passes_gate=passes,
        reason=reason, k=members[0].k)


def _reason(ranking_correct, ends_separated, strong, null, dead, n_criteria) -> str:
    if ranking_correct and ends_separated:
        msg = (f"fit verified: panel ranked correctly; strong end "
               f"[{strong.wilson_low:.2f},{strong.wilson_high:.2f}] separates "
               f"from null end [{null.wilson_low:.2f},{null.wilson_high:.2f}]")
        if dead:
            msg += f"; {len(dead)} non-discriminating criteria flagged for cut"
        return msg
    problems = []
    if not ranking_correct:
        problems.append("panel not ranked strong>weak>null")
    if not ends_separated:
        problems.append(
            f"strong/null intervals overlap "
            f"(strong low {strong.wilson_low:.2f} <= null high {null.wilson_high:.2f})")
    if len(dead) == n_criteria:
        problems.append("every criterion ties the panel — the rubric measures nothing")
    return "does not discriminate: " + "; ".join(problems)


def discrimination_gate(result: DiscriminationResult) -> GateDecision:
    """Deny-by-default gate. Only a positive discrimination result approves."""
    if result.passes_gate:
        reasons = [result.reason]
        if result.non_discriminating:
            reasons.append(
                "cut before ship: " + ", ".join(result.non_discriminating))
        return GateDecision(approved=True, reasons=reasons)
    return GateDecision(approved=False, reasons=[result.reason])


def drop_non_discriminating(draft: DraftRubric,
                            result: DiscriminationResult) -> DraftRubric:
    """The auto-loop's cut: return a new DraftRubric with the panel-tying
    criteria removed, so re-synthesis / re-run works on a leaner rubric. Refuses
    to empty the rubric (returns the draft unchanged if every criterion is dead —
    that rubric must go back to synthesis, not be gutted)."""
    dead = set(result.non_discriminating)
    kept = [c for c in draft.rubric.criteria if c.criterion_id not in dead]
    if not kept or not dead:
        return draft
    new_rubric = Rubric(rubric_id=draft.rubric.rubric_id,
                        version=draft.rubric.version, criteria=kept)
    keep_ids = {c.criterion_id for c in kept}
    return DraftRubric(
        rubric=new_rubric,
        archetype_ids=draft.archetype_ids,
        required_suite_features=draft.required_suite_features,
        core_criterion_ids=[c for c in draft.core_criterion_ids if c in keep_ids],
        ethos_criterion_ids=[c for c in draft.ethos_criterion_ids if c in keep_ids],
        delta_criterion_ids=[c for c in draft.delta_criterion_ids if c in keep_ids],
        reuse_ratio=draft.reuse_ratio,
        conflicts=draft.conflicts,
        provenance={k: v for k, v in draft.provenance.items() if k in keep_ids},
        fit_verified=False,
    )


def render_discrimination_review(result: DiscriminationResult) -> str:
    """Markdown evidence block for the human approval file (Step 42 acceptance)."""
    lines = ["## Discrimination evidence", "",
             f"**Verdict:** {'✅ fit_verified' if result.passes_gate else '❌ does not discriminate'} "
             f"(k={result.k})", "", f"_{result.reason}_", "",
             "### Reference panel (observed pass^k, Wilson 95%)", "",
             "| agent | quality | pass^k | interval |", "|---|---|---|---|"]
    for m in result.members:
        lines.append(f"| {m.agent_id} | {m.quality} | {m.pass_hat_k:.2f} "
                     f"| [{m.wilson_low:.2f}, {m.wilson_high:.2f}] |")
    lines += ["", "### Per-criterion discrimination", "",
              "| criterion | spread | discriminates |", "|---|---|---|"]
    for c in result.per_criterion:
        mark = "yes" if c.discriminates else "**NO — cut**"
        lines.append(f"| {c.criterion_id} | {c.spread:.2f} | {mark} |")
    if result.non_discriminating:
        lines += ["", "> Non-discriminating criteria auto-flagged for cut: "
                  + ", ".join(result.non_discriminating)]
    return "\n".join(lines)
