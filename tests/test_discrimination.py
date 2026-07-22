"""SPEC-9 Step 42 — the discrimination fit gate acceptance tests.

Two layers: pure discrimination math on synthetic panel results (offline), and a
real end-to-end run of a scripted strong/weak/null panel through the actual
scoring engine on a code-only rubric (no LLM).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agenttic.adapters.base import AgentAdapter
from agenttic.rubric_engine.discrimination import (
    CriterionDiscrimination, DiscriminationResult, MemberResult, NullAgent,
    default_panel, discriminate, discrimination_gate,
    drop_non_discriminating, render_discrimination_review, run_reference_panel)
from agenttic.rubric_engine.synthesize import DraftRubric
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import Span, Trace

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# scripted agents for the real run
# --------------------------------------------------------------------------- #

class ScriptedAgent(AgentAdapter):
    def __init__(self, agent_id, plan):
        self.agent_id = agent_id
        self.visibility = "glass_box"
        self._plan = plan          # test_id -> (final_output, [tool names])
        self._n = 0

    def describe(self):
        return {"agent": self.agent_id}

    def run(self, test_input, *, test_case_id=None):
        self._n += 1
        out, tools = self._plan(test_case_id)
        spans = [Span(span_id=f"{self.agent_id}-t{i}-{self._n}", kind="tool_call",
                      name=t, start_time=NOW, end_time=NOW)
                 for i, t in enumerate(tools)]
        spans.append(Span(span_id=f"{self.agent_id}-f-{self._n}",
                          kind="final_output", name="final_output",
                          start_time=NOW, end_time=NOW))
        return Trace(trace_id=f"{self.agent_id}-{test_case_id}-{self._n}",
                     agent_id=self.agent_id, agent_config_hash=self.config_hash(),
                     test_case_id=test_case_id, spans=spans,
                     visibility="glass_box", final_output=out)


CODE_RUBRIC = Rubric(rubric_id="disc-code", version=1, criteria=[
    Criterion(criterion_id="c_output", description="output matches",
              scorer="code", scale="binary", check_ref="final_output_matches_expected"),
    Criterion(criterion_id="c_required", description="required tool called",
              scorer="code", scale="binary", check_ref="required_tool_called",
              tags=["trajectory"]),
    Criterion(criterion_id="c_no_forbidden", description="no forbidden tool",
              scorer="code", scale="binary", check_ref="forbidden_tool_not_called",
              tags=["trajectory"]),
])

CASES = [TestCase(test_id=f"case-{i}", suite_id="disc", task_description="t",
                  expected={"final_output": "DONE", "required_tools": ["lookup"],
                            "forbidden_tools": ["drop_table"]},
                  rubric_id="disc-code") for i in range(6)]


def _strong_plan(tid):
    return ("DONE", ["lookup"])


def _weak_plan(tid):
    even = int(tid.rsplit("-", 1)[1]) % 2 == 0
    return ("DONE" if even else "WRONG", ["lookup"])   # right half the time


def test_end_to_end_panel_run_passes_and_flags_dead_criterion():
    panel = default_panel(ScriptedAgent("strong", _strong_plan),
                          ScriptedAgent("weak", _weak_plan))
    results = run_reference_panel(panel, CASES, CODE_RUBRIC, k=4)
    result = discriminate(results, CODE_RUBRIC)

    # ranks strong > weak > null and separates the ends
    assert result.ranking_correct
    assert result.ends_separated
    assert result.passes_gate
    # c_no_forbidden ties (no one calls the forbidden tool) -> flagged for cut
    assert "c_no_forbidden" in result.non_discriminating
    # c_output separates the panel
    assert "c_output" not in result.non_discriminating
    # the gate approves
    assert discrimination_gate(result).approved is True


def test_null_agent_scores_genuine_failures():
    null = NullAgent()
    from agenttic.scoring.engine import score_run
    rs = score_run(null.run({}, test_case_id="case-0"), CASES[0], CODE_RUBRIC)
    assert rs.passed is False
    # it produced an answer (non-empty) so it is scored, not excluded
    assert rs.scoring_error is None


def test_k_below_four_is_refused():
    panel = default_panel(ScriptedAgent("s", _strong_plan),
                          ScriptedAgent("w", _weak_plan))
    with pytest.raises(ValueError):
        run_reference_panel(panel, CASES, CODE_RUBRIC, k=3)


# --------------------------------------------------------------------------- #
# pure math: synthetic panel results
# --------------------------------------------------------------------------- #

def _member(aid, q, rank, phk, low, high, crit):
    return MemberResult(agent_id=aid, quality=q, quality_rank=rank, n_cases=8,
                        k=4, pass_hat_k=phk, wilson_low=low, wilson_high=high,
                        per_criterion_mean=crit)


TWO_CRIT = Rubric(rubric_id="r", version=1, criteria=[
    Criterion(criterion_id="a", description="x", scorer="code", scale="binary",
              check_ref="exact_match"),
    Criterion(criterion_id="b", description="y", scorer="code", scale="binary",
              check_ref="exact_match")])


def test_every_criterion_ties_fails_gate_with_dead_criteria_named():
    # all three members identical on every criterion and on pass^k
    tie = {"a": 0.5, "b": 0.5}
    members = [_member("strong", "strong", 2, 0.5, 0.3, 0.7, tie),
               _member("weak", "weak", 1, 0.5, 0.3, 0.7, tie),
               _member("null", "null", 0, 0.5, 0.3, 0.7, tie)]
    result = discriminate(members, TWO_CRIT)
    assert result.passes_gate is False
    assert set(result.non_discriminating) == {"a", "b"}       # every criterion dead
    assert "measures nothing" in result.reason
    assert discrimination_gate(result).approved is False       # deny by default


def test_clean_separation_passes_and_can_mark_fit_verified():
    members = [_member("strong", "strong", 2, 0.95, 0.80, 1.0, {"a": 0.95, "b": 0.9}),
               _member("weak", "weak", 1, 0.55, 0.30, 0.78, {"a": 0.5, "b": 0.6}),
               _member("null", "null", 0, 0.10, 0.0, 0.35, {"a": 0.1, "b": 0.0})]
    result = discriminate(members, TWO_CRIT)
    assert result.ranking_correct and result.ends_separated and result.passes_gate
    # evidence renders the panel, ranking and intervals
    md = render_discrimination_review(result)
    assert "Discrimination evidence" in md
    assert "strong" in md and "null" in md and "pass^k" in md
    assert "[0.80, 1.00]" in md          # strong interval rendered


def test_overlapping_intervals_fail_even_if_ordered():
    # ordered by point estimate but intervals overlap -> not shippable
    members = [_member("strong", "strong", 2, 0.6, 0.35, 0.82, {"a": 0.6, "b": 0.6}),
               _member("null", "null", 0, 0.4, 0.18, 0.65, {"a": 0.4, "b": 0.3})]
    result = discriminate(members, TWO_CRIT)
    assert result.ranking_correct is True
    assert result.ends_separated is False
    assert result.passes_gate is False


def test_misranked_panel_fails():
    # the weak agent out-scores the strong one -> the rubric is measuring the
    # wrong thing
    members = [_member("strong", "strong", 2, 0.3, 0.1, 0.55, {"a": 0.3, "b": 0.3}),
               _member("null", "null", 0, 0.8, 0.6, 0.95, {"a": 0.8, "b": 0.8})]
    result = discriminate(members, TWO_CRIT)
    assert result.ranking_correct is False
    assert result.passes_gate is False


def test_drop_non_discriminating_prunes_dead_criteria():
    draft = DraftRubric(
        rubric=Rubric(rubric_id="d", version=1, criteria=TWO_CRIT.criteria),
        archetype_ids=["coding"], required_suite_features=[],
        core_criterion_ids=["a", "b"], provenance={"a": "core:coding", "b": "core:coding"})
    result = DiscriminationResult(
        members=[], ranking_correct=True, ends_separated=True, strong_id="s",
        null_id="n", per_criterion=[
            CriterionDiscrimination("a", 0.5, True, {}),
            CriterionDiscrimination("b", 0.0, False, {})],
        non_discriminating=["b"], passes_gate=True, reason="ok", k=4)
    pruned = drop_non_discriminating(draft, result)
    ids = {c.criterion_id for c in pruned.rubric.criteria}
    assert ids == {"a"}
    assert pruned.core_criterion_ids == ["a"]
    assert "b" not in pruned.provenance


def test_drop_refuses_to_empty_the_rubric():
    draft = DraftRubric(
        rubric=Rubric(rubric_id="d", version=1, criteria=TWO_CRIT.criteria),
        archetype_ids=[], required_suite_features=[])
    result = DiscriminationResult(
        members=[], ranking_correct=False, ends_separated=False, strong_id="s",
        null_id="n", per_criterion=[], non_discriminating=["a", "b"],
        passes_gate=False, reason="all dead", k=4)
    pruned = drop_non_discriminating(draft, result)
    # every criterion is dead -> refuse to gut; return unchanged for re-synthesis
    assert len(pruned.rubric.criteria) == 2
