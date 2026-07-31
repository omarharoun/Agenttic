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
    SCAFFOLD_TAG, DraftRubric, synthesize, synthesize_suite)
# The ONE registry of features this runtime structurally cannot exercise. Imported,
# never restated: a second copy drifts, and a drifted honesty gate reports coverage
# it does not have — the defect it exists to remove.
from agenttic.schema.archetype import UNEXERCISABLE_FEATURES
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
    #: required features for which the suite carries only a labelled placeholder.
    #: NOT a blocker — a scaffold is better than a dropped feature — but it is the
    #: difference between "the rubric's criteria were exercised" and "there is a
    #: slot where the case should go", and the operator approving this draft is
    #: entitled to be told which.
    scaffold_only_features: list[str] = field(default_factory=list)
    #: required feature -> why NO case in this suite can exercise it here, from
    #: :data:`~agenttic.schema.archetype.UNEXERCISABLE_FEATURES`. Distinct from
    #: ``scaffold_only_features`` and deliberately not folded into it: a scaffold
    #: is a slot someone can fill, and this is not. Writing a beautiful multi-turn
    #: case changes nothing while the runtime hands the agent one message. A
    #: feature can be in both lists — today's suite carries it as a placeholder
    #: AND could not exercise it if the placeholder were filled — and the two
    #: sentences an operator needs are different ones.
    unexercisable_features: dict[str, str] = field(default_factory=dict)

    @property
    def fit_verified(self) -> bool:
        return self.draft is not None and self.draft.fit_verified

    @property
    def shippable(self) -> bool:
        return self.state == AWAITING_APPROVAL and self.fit_verified

    def caveats(self) -> list[str]:
        """The qualifications that must travel with this draft's feature list.

        On the result object, not only inside ``review``: ``review`` is markdown
        the CLI writes to a file only when ``--out`` is passed, so the default
        ``agenttic evaluate`` printed the required-feature list with no
        qualification at all — the operator saw a matched suite. One
        implementation (:func:`feature_caveats`), used by the review renderer and
        by any surface that prints the list, because two copies of a disclosure
        are two disclosures that can disagree.
        """
        return feature_caveats(self.scaffold_only_features,
                               self.unexercisable_features)


@dataclass
class FeatureEvidence:
    """What a suite's ``feature:`` tags actually establish — three states, not two.

    ``named`` is bookkeeping: every feature some case claims, and the ONLY thing
    the absence gate means by "there is a case for this". The three that a reader
    of the draft needs are different in kind, and collapsing any two of them is
    how a tag became a coverage claim:

    * ``exercised`` — a real (non-scaffold) case names it AND the runtime can
      deliver it. The only one of the three that is evidence.
    * ``scaffold_only`` — named exclusively by a labelled placeholder. A fillable
      slot: writing the case moves it into ``exercised``.
    * ``unexercisable`` — feature -> why nothing here can exercise it, whoever
      wrote the case. NOT a slot; filling it changes nothing, because the gap is
      in the runtime. Reporting these as scaffolds would send an operator to fill
      a placeholder that cannot help, which is why they get their own list.
    """

    named: set[str] = field(default_factory=set)
    exercised: set[str] = field(default_factory=set)
    scaffold_only: set[str] = field(default_factory=set)
    unexercisable: dict[str, str] = field(default_factory=dict)


def audit_features(cases: list[TestCase]) -> FeatureEvidence:
    """Split the ``feature:`` tags in ``cases`` by what they actually establish.

    Two independent ways a tag can fail to be evidence, and until now only the
    first was caught:

    * **The case is a placeholder.** ``_scaffold_case`` writes ``feature:<f>`` on
      itself, so reading "covered" off the tag made a suite of empty scaffolds
      report as perfectly matched. :data:`SCAFFOLD_TAG` splits that.
    * **The runtime cannot deliver the situation.** The scaffold marker is about
      who WROTE the case; it says nothing about what the harness can do with it.
      A generator-produced case tagged ``feature:multi_turn_state`` carries no
      marker and so read as exercised — but a ``TestCase`` is one ``input`` dict
      handed to ``AgentAdapter.run`` as one message, and nothing speaks a second
      time, so there was no second turn for state to be held across. The tag was
      an intention; the credit was for an intention.

    The second is what :data:`~agenttic.schema.archetype.UNEXERCISABLE_FEATURES`
    names, and it is imported rather than restated so there is one place a claim
    about the runtime can be made — and one place it must be removed from when
    the runtime actually changes.
    """
    named: set[str] = set()
    carried: set[str] = set()          # a NON-scaffold case names it
    for c in cases:
        feats = {t.split(":", 1)[1] for t in c.tags if t.startswith("feature:")}
        named |= feats
        if SCAFFOLD_TAG not in c.tags:
            carried |= feats
    return FeatureEvidence(
        named=named,
        # a real case is necessary and no longer sufficient
        exercised=carried - set(UNEXERCISABLE_FEATURES),
        # unchanged: "the only case for this feature is a placeholder". Derived
        # from `carried`, never from `exercised` — an unexercisable feature with a
        # real case has no placeholder to fill, and calling it scaffold-only would
        # offer a remedy that does not exist.
        scaffold_only=named - carried,
        unexercisable={f: why for f, why in UNEXERCISABLE_FEATURES.items()
                       if f in named})


def feature_coverage(cases: list[TestCase]) -> tuple[set[str], set[str]]:
    """``(exercised, scaffold_only)`` — the two-set view of :func:`audit_features`.

    ``scaffold_only`` is bit-identical to what it always was. ``exercised`` no
    longer includes a feature this runtime cannot deliver, however real the case
    that names it: see :func:`audit_features`. Callers that need to tell those
    two apart — an empty slot versus a structural gap — must use
    :func:`audit_features`; this signature keeps the common case one line.
    """
    ev = audit_features(cases)
    return ev.exercised, ev.scaffold_only


def feature_caveats(scaffold_only: list[str],
                    unexercisable: dict[str, str]) -> list[str]:
    """The operator-facing sentences qualifying a required-feature list.

    Rendered from the data rather than written at each surface, so the console,
    the review markdown and anything added later say the same thing. Order is
    deliberate: the structural gap first, because it is the one no amount of work
    on this suite will close. ``scaffold_only`` is a LIST because its order is the
    caller's — the required-feature order the operator just read — not something
    to re-sort here.
    """
    lines: list[str] = []
    for f, why in sorted(unexercisable.items()):
        lines.append(
            f"UNEXERCISABLE: {f} — {why}. No case in this suite exercises it and "
            f"none can; a criterion resting on it is not demonstrated by any "
            f"result from this run. Filling a placeholder will not change that — "
            f"the runtime has to.")
    fillable = [f for f in scaffold_only if f not in unexercisable]
    if fillable:
        lines.append(
            f"scaffold-only features: {', '.join(fillable)} — the suite names "
            f"these and contains no case that exercises them. A criterion resting "
            f"on one of them has not been demonstrated by this suite; the "
            f"placeholder has to be filled before a result here is evidence about "
            f"that feature.")
    return lines


def integrity_check(draft: DraftRubric,
                    cases: list[TestCase]) -> tuple[bool, list[str]]:
    """SPEC-6-style integrity: the rubric's code checks resolve, and the suite
    carries a case for every required feature.

    Blocks on a feature with NO case at all — deliberately unchanged, so no
    existing flow regresses. A feature carried only by a scaffold is reported by
    ``evaluate`` as ``scaffold_only_features`` instead of being counted as
    matched; promoting that to a hard block is a decision for whenever these
    features become generatable, not something to do by loosening a gate.

    The absence test reads ``FeatureEvidence.named`` — every feature ANY case
    claims — rather than ``exercised | scaffold_only``. Those were the same set
    until ``exercised`` narrowed to drop features the runtime cannot deliver, and
    the difference matters in both directions: this gate must not start blocking a
    draft because a feature is unexercisable (nothing is missing — the case is
    there, and no suite can fix the harness), and it must not stop blocking one
    that genuinely has no case. Unexercisability is disclosed, loudly and by name,
    through ``unexercisable_features``; it is not the same finding as an empty
    suite and must not be reported through the same channel."""
    from agenttic.scoring.checks import validate_rubric_checks
    problems: list[str] = []
    try:
        validate_rubric_checks(draft.rubric)
    except Exception as e:                       # unknown check_ref etc.
        problems.append(f"rubric check invalid: {e}")
    named = audit_features(cases).named
    absent = [f for f in draft.required_suite_features if f not in named]
    if absent:
        problems.append(f"suite has no case at all for required features: {absent}")
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
    # Reported in every downstream state, including the failed ones: an operator
    # reading a blocked draft needs to know the placeholders are there too.
    ev = audit_features(cases)
    scaffold_only = [f for f in draft.required_suite_features
                     if f in ev.scaffold_only]
    # Scoped to the REQUIRED features, like the line above: this is a statement
    # about the rubric's own demands. `ev.unexercisable` is the suite-wide view
    # for anything that needs it. A required feature can appear in both lists —
    # only a placeholder today, and unexercisable even once filled — and both
    # sentences are true.
    unexercisable = {f: why for f, why in ev.unexercisable.items()
                     if f in draft.required_suite_features}
    if not ok:
        return EvaluationDraft(
            state=INTEGRITY_FAILED, matches=matches, draft=draft, suite=suite,
            cases=cases, reasons=problems,
            scaffold_only_features=scaffold_only,
            unexercisable_features=unexercisable)

    # discrimination gate + auto-loop (cut dead criteria, retry).
    result: DiscriminationResult | None = None
    if discriminate_fn is None:
        return EvaluationDraft(
            state=AWAITING_DISCRIMINATION, matches=matches, draft=draft,
            suite=suite, cases=cases,
            reasons=["no reference panel supplied — cannot prove the rubric "
                     "discriminates (Hard Rule 39). Supply a panel to run the gate."],
            scaffold_only_features=scaffold_only,
            unexercisable_features=unexercisable,
            review=_review(matches, draft, None, scaffold_only, unexercisable))

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
            scaffold_only_features=scaffold_only,
            unexercisable_features=unexercisable,
            review=_review(matches, draft, result, scaffold_only, unexercisable))

    return EvaluationDraft(
        state=AWAITING_APPROVAL, matches=matches, draft=draft, suite=suite,
        cases=cases, discrimination=result,
        scaffold_only_features=scaffold_only,
        unexercisable_features=unexercisable,
        review=_review(matches, draft, result, scaffold_only, unexercisable))


def _review(matches, draft: DraftRubric | None,
            result: DiscriminationResult | None,
            scaffold_only: list[str] | None = None,
            unexercisable: dict[str, str] | None = None) -> str:
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
        # One renderer for these sentences (feature_caveats) — the review and the
        # console must not be able to qualify the same list differently.
        lines += [f"- ⚠ **{c}**" for c in
                  feature_caveats(scaffold_only or [], unexercisable or {})]
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
