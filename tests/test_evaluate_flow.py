"""SPEC-9 Step 44 — one-call operator flow acceptance tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agenttic.adapters.base import AgentAdapter
from agenttic.rubric_engine.classify import ClassifyInputs
from agenttic.rubric_engine.discrimination import DiscriminationResult
from agenttic.rubric_engine.evaluate import (
    AWAITING_APPROVAL, AWAITING_DISCRIMINATION, CANNOT_DISCRIMINATE,
    NEEDS_GENERATION, approve_and_run, evaluate)
from agenttic.schema.scorecard import CriterionScore, Scorecard
from agenttic.schema.trace import Span, Trace

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

PILOT = ("A customer support chat agent for an online store: multi-turn, looks "
         "up an order, processes a refund, updates the account, follows the refund "
         "policy, and escalates to a human when the policy does not cover a case.")


def _passing(draft) -> DiscriminationResult:
    from agenttic.rubric_engine.discrimination import CriterionDiscrimination
    pc = [CriterionDiscrimination(c.criterion_id, 0.4, True, {})
          for c in draft.rubric.criteria]
    return DiscriminationResult(
        members=[], ranking_correct=True, ends_separated=True, strong_id="strong",
        null_id="null", per_criterion=pc, non_discriminating=[],
        passes_gate=True, reason="fit verified", k=4)


def _failing(draft) -> DiscriminationResult:
    return DiscriminationResult(
        members=[], ranking_correct=False, ends_separated=False, strong_id="s",
        null_id="n", per_criterion=[], non_discriminating=[],
        passes_gate=False, reason="panel not ranked strong>weak>null", k=4)


class FakeJudge:
    def score_criterion(self, criterion, trace, tc):
        return CriterionScore(criterion_id=criterion.criterion_id, score=1.0,
                              scorer="judge")


class SupportAgent(AgentAdapter):
    agent_id = "support-strong"
    visibility = "glass_box"

    def describe(self):
        return {"agent": self.agent_id}

    def run(self, test_input, *, test_case_id=None):
        spans = [Span(span_id=f"l-{test_case_id}", kind="llm_call", name="llm",
                      start_time=NOW, end_time=NOW),
                 Span(span_id=f"f-{test_case_id}", kind="final_output",
                      name="final_output", start_time=NOW, end_time=NOW)]
        return Trace(trace_id=f"t-{test_case_id}", agent_id=self.agent_id,
                     agent_config_hash="h", test_case_id=test_case_id, spans=spans,
                     visibility="glass_box", final_output="Resolved per policy.")


def test_pilot_produces_fit_verified_draft_awaiting_approval():
    result = evaluate(ClassifyInputs(agent_description=PILOT),
                      discriminate_fn=_passing)
    assert result.state == AWAITING_APPROVAL
    assert result.fit_verified and result.shippable
    # classification present
    assert result.matches[0].archetype_id == "conversational_transactional"
    # reuse% + discrimination evidence attached in the review
    assert "reuse" in result.review.lower()
    assert "Classification" in result.review
    assert "Discrimination evidence" in result.review
    # ≥60% reused proven criteria
    assert result.draft.reuse_ratio >= 0.6
    # a matched suite came with it
    assert result.suite is not None and result.cases


def test_cannot_classify_surfaces_actionable_state_not_a_bad_rubric():
    result = evaluate(ClassifyInputs(agent_description="plays chess with the user"),
                      discriminate_fn=_passing)
    assert result.state == NEEDS_GENERATION
    assert result.draft is None                 # no silent rubric emitted
    assert result.reasons                        # actionable message


def test_cannot_discriminate_surfaces_failing_state():
    result = evaluate(ClassifyInputs(agent_description=PILOT),
                      discriminate_fn=_failing, max_rounds=2)
    assert result.state == CANNOT_DISCRIMINATE
    assert result.fit_verified is False
    assert not result.shippable
    assert any("ranked" in r for r in result.reasons)


def test_no_panel_yields_awaiting_discrimination_not_shippable():
    result = evaluate(ClassifyInputs(agent_description=PILOT))
    assert result.state == AWAITING_DISCRIMINATION
    assert not result.shippable                  # Hard Rule 39: no fit proof, no ship
    assert result.draft is not None              # but the draft is built + reviewable


def test_end_to_end_inputs_to_approved_to_scored_enforces_gates():
    result = evaluate(ClassifyInputs(agent_description=PILOT),
                      discriminate_fn=_passing)
    assert result.shippable
    card = approve_and_run(result, SupportAgent(), judge=FakeJudge())
    assert isinstance(card, Scorecard)
    assert 0.0 <= card.task_success_rate <= 1.0
    assert card.rubric_id == result.draft.rubric.rubric_id


# --------------------------------------------------------------------------- #
# A scaffold names a feature; it does not exercise it.
# --------------------------------------------------------------------------- #

def test_a_scaffold_does_not_exercise_the_feature_it_names():
    """The suite-match check used to read `covered` off the very `feature:` tag
    the scaffold writes on itself, while `synthesize_suite` emitted a scaffold
    for every otherwise-uncovered required feature. `missing` was therefore
    provably always empty: the gate could not fire, and a suite of empty
    placeholders reported as matched."""
    from agenttic.rubric_engine.evaluate import feature_coverage
    from agenttic.rubric_engine.synthesize import SCAFFOLD_TAG

    result = evaluate(ClassifyInputs(agent_description=PILOT),
                      discriminate_fn=_passing)
    scaffolds = [c for c in result.cases if SCAFFOLD_TAG in c.tags]
    assert scaffolds, "the pilot draft is carried entirely by scaffolds today"

    exercised, scaffold_only = feature_coverage(result.cases)
    assert exercised == set(), "no case in this suite exercises anything"
    assert "multi_turn_state" in scaffold_only
    # every required feature is accounted for, and none of it as evidence
    assert set(result.draft.required_suite_features) <= scaffold_only


def test_scaffold_only_features_are_named_in_the_review():
    """The operator approving the draft is told which features are slots."""
    result = evaluate(ClassifyInputs(agent_description=PILOT),
                      discriminate_fn=_passing)
    assert "multi_turn_state" in result.scaffold_only_features
    assert "scaffold-only features" in result.review
    assert "multi_turn_state" in result.review
    # and it is reported on the blocked paths too, not only the happy one
    no_panel = evaluate(ClassifyInputs(agent_description=PILOT))
    assert no_panel.scaffold_only_features == result.scaffold_only_features
    assert "scaffold-only features" in no_panel.review


def test_a_real_case_for_a_feature_is_not_reported_as_scaffold_only():
    """The split has to be on the scaffold marker, not on the feature name —
    otherwise filling the placeholder in would never change the report."""
    from agenttic.rubric_engine.evaluate import feature_coverage
    from agenttic.rubric_engine.synthesize import SCAFFOLD_TAG, _scaffold_case
    from agenttic.schema.testcase import TestCase

    real = TestCase(test_id="t-real", suite_id="s", version=1,
                    task_description="an actual escalation case",
                    input={"request": "I want to speak to a manager."},
                    tags=["edge_case", "feature:should_escalate"],
                    rubric_id="r")
    placeholder = _scaffold_case("s", "r", "multi_turn_state", 0)
    assert SCAFFOLD_TAG in placeholder.tags

    exercised, scaffold_only = feature_coverage([real, placeholder])
    assert exercised == {"should_escalate"}
    assert scaffold_only == {"multi_turn_state"}


def test_a_feature_with_no_case_at_all_still_fails_integrity():
    """Blocking behaviour is deliberately unchanged: a scaffold is not a pass,
    but it is also not the thing integrity_check was ever blocking on."""
    from agenttic.rubric_engine.evaluate import integrity_check

    result = evaluate(ClassifyInputs(agent_description=PILOT),
                      discriminate_fn=_passing)
    ok, problems = integrity_check(result.draft, result.cases)
    assert ok and not problems              # scaffolds still satisfy the gate

    ok, problems = integrity_check(result.draft, [])
    assert not ok
    assert any("no case at all" in p for p in problems)
    assert any("multi_turn_state" in p for p in problems)


def test_approve_refuses_unverified_rubric_unless_waived():
    result = evaluate(ClassifyInputs(agent_description=PILOT))  # awaiting_discrimination
    assert not result.fit_verified
    with pytest.raises(ValueError):
        approve_and_run(result, SupportAgent(), judge=FakeJudge())
    # explicit waiver is the only override, and it is recorded on the id
    card = approve_and_run(result, SupportAgent(), judge=FakeJudge(),
                           waiver="operator override for pilot smoke test")
    assert "waived" in card.scorecard_id


# --------------------------------------------------------------------------- #
# A tag is not evidence either. The residual circularity, closed.
#
# The scaffold marker split "named" from "exercised", but it is a fact about who
# WROTE a case, not about what the harness can do with it. A generator-produced
# case tagged `feature:multi_turn_state` carries no marker, so it read as
# exercised — while `AgentAdapter.run` takes one `input` dict and delivers it as
# ONE message, and nothing speaks a second time. There was no second turn for
# state to be held across. The credit was for the generator's intention.
# --------------------------------------------------------------------------- #

class MultiTurnGenerator:
    """A generator that produces a REAL (non-scaffold) multi-turn case. Nothing
    is wrong with the case; the runtime cannot deliver it."""

    def define_criteria(self, task, rubric_id):
        from agenttic.schema.rubric import Criterion, Rubric
        return Rubric(rubric_id=rubric_id, version=1, criteria=[
            Criterion(criterion_id="dom_holds_state",
                      description="Remembers the order id from the earlier turn.",
                      scorer="judge", scale="binary",
                      anchors={"pass": "Recalls it.", "fail": "Asks again."})])

    def generate_cases(self, task, *, suite_id, rubric):
        from agenttic.schema.testcase import TestCase
        return [TestCase(
            test_id=f"{suite_id}-mt", suite_id=suite_id, version=1,
            task_description="a multi-turn case requiring state across turns",
            input={"turn_1": "I want a refund", "turn_2": "the May order"},
            tags=["edge_case", "feature:multi_turn_state"],
            rubric_id=rubric.rubric_id)]


def test_a_real_case_for_an_unexercisable_feature_is_not_exercised():
    """The defect, at the unit that decides it. `feature_coverage([real])`
    returned `{"multi_turn_state"}` — a tag, promoted to evidence."""
    from agenttic.rubric_engine.evaluate import audit_features, feature_coverage
    from agenttic.schema.testcase import TestCase

    real = TestCase(test_id="t-mt", suite_id="s", version=1,
                    task_description="a multi-turn case requiring state",
                    input={"turn_1": "a", "turn_2": "b"},
                    tags=["edge_case", "feature:multi_turn_state"], rubric_id="r")
    exercised, scaffold_only = feature_coverage([real])
    assert exercised == set()
    # and it is NOT relabelled as a scaffold: there is no placeholder to fill,
    # so offering that remedy would be a second wrong answer
    assert scaffold_only == set()
    ev = audit_features([real])
    assert ev.named == {"multi_turn_state"}          # the case is really there
    assert "multi_turn_state" in ev.unexercisable
    assert ev.unexercisable["multi_turn_state"].strip()


def test_the_registry_is_imported_never_copied():
    """A duplicated list of runtime limitations drifts, and a drifted honesty
    gate reports coverage it does not have — the defect class being removed."""
    import inspect

    import agenttic.rubric_engine.evaluate as m
    from agenttic.schema.archetype import UNEXERCISABLE_FEATURES

    assert m.UNEXERCISABLE_FEATURES is UNEXERCISABLE_FEATURES
    src = inspect.getsource(m)
    assert "UNEXERCISABLE_FEATURES: dict" not in src      # no second declaration
    for reason in UNEXERCISABLE_FEATURES.values():
        assert reason not in src                          # no restated reason


def test_the_generator_path_now_reports_the_feature_as_unsatisfied():
    """The honest outcome, stated as a test rather than avoided: this suite HAS a
    real multi-turn case and still reports multi_turn_state as not exercised."""
    from agenttic.rubric_engine.evaluate import feature_coverage

    result = evaluate(ClassifyInputs(agent_description=PILOT),
                      business_context=PILOT, generator=MultiTurnGenerator(),
                      discriminate_fn=_passing)
    mt = [c for c in result.cases if "feature:multi_turn_state" in c.tags]
    assert len(mt) == 1 and "scaffold" not in mt[0].tags     # a real case
    exercised, _ = feature_coverage(result.cases)
    assert "multi_turn_state" not in exercised
    assert "multi_turn_state" in result.unexercisable_features


def test_an_unexercisable_feature_does_not_newly_block_the_integrity_gate():
    """Blocking behaviour is unchanged in BOTH directions. The gate blocks on a
    feature with no case at all; a feature with a case the runtime cannot deliver
    is a different finding and must not be reported through that channel — the
    suite is not missing anything, the harness is."""
    from agenttic.rubric_engine.evaluate import integrity_check

    result = evaluate(ClassifyInputs(agent_description=PILOT),
                      business_context=PILOT, generator=MultiTurnGenerator(),
                      discriminate_fn=_passing)
    ok, problems = integrity_check(result.draft, result.cases)
    assert ok and not problems
    assert result.state == AWAITING_APPROVAL

    # ...and it still blocks when the case really is absent
    ok, problems = integrity_check(result.draft, [])
    assert not ok and any("no case at all" in p for p in problems)


def test_scaffold_only_is_unchanged_by_the_narrowing():
    """`exercised` narrowed; `scaffold_only` must not have moved. It is derived
    from whether a NON-scaffold case exists, never from `exercised` — otherwise a
    structural gap would start masquerading as a fillable slot."""
    from agenttic.rubric_engine.evaluate import feature_coverage
    from agenttic.rubric_engine.synthesize import _scaffold_case
    from agenttic.schema.testcase import TestCase

    real = TestCase(test_id="t-esc", suite_id="s", version=1,
                    task_description="an actual escalation case",
                    input={"request": "I want a manager."},
                    tags=["edge_case", "feature:should_escalate"], rubric_id="r")
    cases = [real, _scaffold_case("s", "r", "multi_turn_state", 0),
             _scaffold_case("s", "r", "policy_doc", 1)]
    exercised, scaffold_only = feature_coverage(cases)
    assert exercised == {"should_escalate"}
    assert scaffold_only == {"multi_turn_state", "policy_doc"}


# --------------------------------------------------------------------------- #
# The disclosure has to reach the operator, not just the markdown file.
# --------------------------------------------------------------------------- #

def test_the_caveats_are_on_the_result_not_only_inside_the_review():
    """`review` is markdown the CLI writes only when `--out` is passed, so the
    default `agenttic evaluate` printed the required-feature list with nothing
    qualifying it. The data and its rendering both live on the result."""
    result = evaluate(ClassifyInputs(agent_description=PILOT),
                      discriminate_fn=_passing)
    assert result.unexercisable_features                      # data
    caveats = result.caveats()                                # rendering
    assert caveats
    assert any("multi_turn_state" in c and "no second turn" in c for c in caveats)
    assert any("scaffold-only features" in c for c in caveats)


def test_the_review_and_the_caveats_are_one_implementation():
    """Two copies of a disclosure are two disclosures that can disagree."""
    result = evaluate(ClassifyInputs(agent_description=PILOT),
                      discriminate_fn=_passing)
    for c in result.caveats():
        assert c in result.review


def test_the_caveats_are_reported_on_the_blocked_paths_too():
    no_panel = evaluate(ClassifyInputs(agent_description=PILOT))
    assert no_panel.state == AWAITING_DISCRIMINATION
    assert no_panel.unexercisable_features
    assert no_panel.caveats()


def test_a_draft_with_nothing_to_disclose_says_nothing():
    """The caveats are findings. A clean draft must not carry a decorative
    paragraph that trains operators to skip the section."""
    from agenttic.rubric_engine.evaluate import feature_caveats
    assert feature_caveats([], {}) == []
