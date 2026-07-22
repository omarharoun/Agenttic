"""One-call operator flow (SPEC-9 Step 44) — the whole engine behind one call.

``evaluate(inputs)`` runs: classify -> synthesize (rubric + matched suite) ->
integrity gates -> discrimination gate (with an auto-loop that cuts dead criteria
and retries) -> present a finished, fit-verified draft with its evidence for human
approval. On approve, run. The operator's job is judgment on a finished artifact,
not assembly.

The unhappy paths surface a clear, actionable state — never a silent bad rubric
(Hard Rule 39: nothing ships without passing the discrimination gate or an
explicit, recorded waiver).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agenttic.rubric_engine.classify import (
    CUSTOM, ArchetypeMatch, ClassifyInputs, classify)
from agenttic.rubric_engine.discrimination import (
    DiscriminationResult, drop_non_discriminating, render_discrimination_review)
from agenttic.rubric_engine.synthesize import (
    DraftRubric, synthesize, synthesize_suite)
from agenttic.schema.scorecard import RunScore, Scorecard
from agenttic.schema.testcase import TestCase, TestSuite

# States the operator sees. Only AWAITING_APPROVAL is a shippable, fit-verified
# artifact; every other terminal state is actionable and blocks the run.
AWAITING_APPROVAL = "awaiting_approval"
NEEDS_GENERATION = "needs_generation"         # custom agent, no generator to build a delta
AWAITING_DISCRIMINATION = "awaiting_discrimination"  # no panel supplied to prove fit
INTEGRITY_FAILED = "integrity_failed"
CANNOT_DISCRIMINATE = "cannot_discriminate"   # gate failed after the auto-loop

DiscriminateFn = Callable[[DraftRubric], DiscriminationResult]


@dataclass
class EvaluationDraft:
    """The finished (or blocked) artifact the operator judges."""

    state: str
    matches: list[ArchetypeMatch]
    reasons: list[str] = field(default_factory=list)
    draft: DraftRubric | None = None
    suite: TestSuite | None = None
    cases: list[TestCase] = field(default_factory=list)
    discrimination: DiscriminationResult | None = None
    review: str = ""

    @property
    def fit_verified(self) -> bool:
        return self.draft is not None and self.draft.fit_verified

    @property
    def shippable(self) -> bool:
        return self.state == AWAITING_APPROVAL and self.fit_verified


def integrity_check(draft: DraftRubric,
                    cases: list[TestCase]) -> tuple[bool, list[str]]:
    """SPEC-6-style integrity: the rubric's code checks resolve, and the suite is
    matched to the rubric (every required feature is exercised by a case)."""
    from agenttic.scoring.checks import validate_rubric_checks
    problems: list[str] = []
    try:
        validate_rubric_checks(draft.rubric)
    except Exception as e:                       # unknown check_ref etc.
        problems.append(f"rubric check invalid: {e}")
    covered = {t.split(":", 1)[1] for c in cases for t in c.tags
               if t.startswith("feature:")}
    missing = [f for f in draft.required_suite_features if f not in covered]
    if missing:
        problems.append(f"suite does not exercise required features: {missing}")
    return (not problems), problems


def evaluate(
    inputs: ClassifyInputs,
    *,
    business_context: str = "",
    generator=None,
    client=None,
    threshold: float = 0.5,
    discriminate_fn: DiscriminateFn | None = None,
    max_rounds: int = 2,
    suite_id: str = "eval-suite",
    archetypes=None,
    cores=None,
) -> EvaluationDraft:
    business_context = business_context or inputs.business_doc or inputs.agent_description
    matches = classify(inputs, client=client, threshold=threshold, archetypes=archetypes)

    # (unhappy) a custom agent with no generator can't have its delta built.
    only_custom = all(m.archetype_id == CUSTOM for m in matches)
    if only_custom and generator is None:
        return EvaluationDraft(
            state=NEEDS_GENERATION, matches=matches,
            reasons=["no archetype matched and no generator supplied to build a "
                     "rubric from scratch — provide a fuller description or a "
                     "generator (custom-archetype path)"])

    draft = synthesize(matches, business_context, generator=generator,
                       archetypes=archetypes, cores=cores)
    suite, cases = synthesize_suite(
        draft, suite_id=suite_id, business_context=business_context,
        generator=generator)

    ok, problems = integrity_check(draft, cases)
    if not ok:
        return EvaluationDraft(
            state=INTEGRITY_FAILED, matches=matches, draft=draft, suite=suite,
            cases=cases, reasons=problems)

    # discrimination gate + auto-loop (cut dead criteria, retry).
    result: DiscriminationResult | None = None
    if discriminate_fn is None:
        return EvaluationDraft(
            state=AWAITING_DISCRIMINATION, matches=matches, draft=draft,
            suite=suite, cases=cases,
            reasons=["no reference panel supplied — cannot prove the rubric "
                     "discriminates (Hard Rule 39). Supply a panel to run the gate."],
            review=_review(matches, draft, None))

    for _ in range(max(1, max_rounds)):
        result = discriminate_fn(draft)
        if result.passes_gate:
            draft.fit_verified = True
            break
        pruned = drop_non_discriminating(draft, result)
        if pruned is draft:                      # nothing left to cut -> stuck
            break
        draft = pruned

    if result is None or not result.passes_gate:
        named = result.non_discriminating if result else []
        return EvaluationDraft(
            state=CANNOT_DISCRIMINATE, matches=matches, draft=draft, suite=suite,
            cases=cases, discrimination=result,
            reasons=[(result.reason if result else "discrimination did not run"),
                     f"failing/dead criteria: {named}"],
            review=_review(matches, draft, result))

    return EvaluationDraft(
        state=AWAITING_APPROVAL, matches=matches, draft=draft, suite=suite,
        cases=cases, discrimination=result, review=_review(matches, draft, result))


def _review(matches, draft: DraftRubric | None,
            result: DiscriminationResult | None) -> str:
    lines = ["# Evaluation draft — awaiting approval", "",
             "## Classification"]
    for m in matches:
        lines.append(f"- **{m.archetype_id}** — confidence {m.confidence:.2f} "
                     f"({m.source}); {m.rationale}")
    if draft is not None:
        s = draft.feature_summary()
        lines += ["", "## Rubric", "",
                  f"- archetypes composed: {', '.join(draft.archetype_ids) or '(custom)'}",
                  f"- criteria: {s['n_criteria']} "
                  f"({s['n_core']} core + {s['n_ethos']} ethos + {s['n_delta']} delta)",
                  f"- **reuse: {s['reuse_ratio']*100:.0f}%** proven criteria "
                  f"(delta is {s['delta_weight_fraction']*100:.0f}% by weight)",
                  f"- required suite features: {', '.join(draft.required_suite_features)}"]
        if draft.conflicts:
            lines.append(f"- ⚠ cross-archetype conflicts (first-wins): {draft.conflicts}")
    if result is not None:
        lines += ["", render_discrimination_review(result)]
    return "\n".join(lines)


def approve_and_run(
    state: EvaluationDraft,
    adapter,
    *,
    judge=None,
    k: int = 1,
    reg=None,
    waiver: str | None = None,
) -> Scorecard:
    """On approval, run the agent under test through the approved suite+rubric.

    Enforces the gates: refuses unless the draft is fit-verified (Hard Rule 39),
    UNLESS an explicit ``waiver`` reason is supplied (recorded on the scorecard's
    id). Never runs an integrity-failed or unclassified draft."""
    if state.draft is None or state.suite is None:
        raise ValueError(f"cannot run: draft is in state {state.state}")
    if not state.fit_verified and waiver is None:
        raise ValueError(
            f"refusing to run a rubric that has not passed the discrimination "
            f"gate (state={state.state}). Supply waiver=... to override (recorded).")

    from agenttic.scoring.checks import CheckConfigError
    from agenttic.scoring.engine import score_run
    rubric = state.draft.rubric
    run_scores: list[RunScore] = []
    for tc in state.cases:
        for _ in range(max(1, k)):
            trace = adapter.run(tc.input, test_case_id=tc.test_id)
            try:
                run_scores.append(score_run(trace, tc, rubric, judge))
            except CheckConfigError as e:
                # a scaffold/placeholder case the human/generator hasn't filled
                # yet can't be scored — record it as an ERRORED run (excluded from
                # aggregates), never a crash. Mirrors ops.score_op.
                run_scores.append(RunScore(
                    trace_id=trace.trace_id, test_id=tc.test_id,
                    criterion_scores=[], passed=False,
                    cost_usd=trace.total_cost_usd,
                    latency_ms=trace.total_latency_ms, steps=trace.total_steps,
                    scoring_error=str(e)))
    sid = f"eval-{state.suite.suite_id}" + ("-waived" if waiver else "")
    card = Scorecard.aggregate(
        scorecard_id=sid, agent_id=getattr(adapter, "agent_id", "agent"),
        suite_id=state.suite.suite_id, suite_version=state.suite.version,
        rubric_id=rubric.rubric_id, rubric_version=rubric.version,
        run_scores=run_scores, visibility_tier="glass_box")
    return card
