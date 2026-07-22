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


def test_approve_refuses_unverified_rubric_unless_waived():
    result = evaluate(ClassifyInputs(agent_description=PILOT))  # awaiting_discrimination
    assert not result.fit_verified
    with pytest.raises(ValueError):
        approve_and_run(result, SupportAgent(), judge=FakeJudge())
    # explicit waiver is the only override, and it is recorded on the id
    card = approve_and_run(result, SupportAgent(), judge=FakeJudge(),
                           waiver="operator override for pilot smoke test")
    assert "waived" in card.scorecard_id
