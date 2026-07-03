"""LLM-judge quality & RAG rubric evaluators (``metrics.judge_quality``).

Every test scores through the REAL judge harness (``LLMJudge``) driven by a
FAKE/mock client — NO API key, no tokens spent. Covers:
- each family criterion is a valid anchored judge Criterion (Hard Rules 2 & 3);
- each scores through the harness on both allowed values of its scale;
- the judge prompt actually carries the anchors, the task input, and (for RAG
  metrics) the reference context;
- every criterion defaults PROVISIONAL (Hard Rule 6) — none is in the
  demonstrated-calibrated judge set and all are flagged by uncalibrated_criteria;
- ids don't collide with the existing calibrated judge criteria;
- the catalog registration hook exposes the family with honest fields.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from ascore.metrics import judge_quality as jq
from ascore.metrics.catalog import catalog_payload
from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.testcase import TestCase
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.scoring.judge import ALLOWED_SCORES, JudgeError, LLMJudge

NOW = datetime(2026, 7, 3, tzinfo=timezone.utc)

CONTEXT = ("Paris is the capital of France. The Eiffel Tower is in Paris and was "
           "completed in 1889.")


def make_trace(final="The Eiffel Tower is in Paris, completed in 1889."):
    spans = [Span(span_id="f", kind="final_output", name="final_output",
                  start_time=NOW, end_time=NOW)]
    return Trace(trace_id="tr-jq", agent_id="agent", agent_config_hash="h",
                 test_case_id="tc-jq", spans=spans, visibility="black_box",
                 final_output=final, schema_version=SCHEMA_VERSION)


def make_tc(with_context=True):
    inp = {"request": "Where is the Eiffel Tower and when was it completed?"}
    expected = {}
    if with_context:
        inp["context"] = CONTEXT
        inp["reference_context"] = CONTEXT
        expected["reference_context"] = CONTEXT
    return TestCase(test_id="tc-jq", suite_id="s", task_description="grounded q",
                    input=inp, expected=expected, rubric_id="std-judge-quality-v1")


class FakeJudgeClient:
    """Captures messages.create kwargs; replies are raw JSON strings in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        return NS(content=[NS(type="text", text=self.replies.pop(0))],
                  usage=NS(input_tokens=100, output_tokens=20))


def make_judge(replies, **kw):
    defaults = dict(model="judge-model", agent_model="agent-model")
    defaults.update(kw)
    return LLMJudge(client=FakeJudgeClient(replies), **defaults)


def verdict(score, rationale="ok"):
    return json.dumps({"score": score, "rationale": rationale})


# --------------------------------------------------------------------------- #
# Family shape / schema
# --------------------------------------------------------------------------- #

class TestFamilyShape:
    def test_expected_metrics_present(self):
        ids = set(jq.criterion_ids())
        assert ids == {
            "groundedness_judge", "answer_relevance_judge", "context_relevance_judge",
            "hallucination_free_judge", "completeness_judge", "coherence_judge",
            "conciseness_judge", "tone_professional_judge", "helpfulness_judge",
            "instruction_following_judge", "refusal_appropriateness_judge",
            "summarization_quality_judge",
        }

    def test_all_criteria_are_valid_judge_criteria(self):
        # Building each Criterion already enforces Hard Rule 2 (pass/fail anchors);
        # assert scorer + scale here (Hard Rule 3).
        for c in jq.criteria():
            assert isinstance(c, Criterion)
            assert c.scorer == "judge"
            assert c.scale in ("binary", "three_point")
            assert set(c.anchors) >= {"pass", "fail"}
            assert c.anchors["pass"] and c.anchors["fail"]

    def test_ids_do_not_collide_with_existing_calibrated_judge_criteria(self):
        # existing calibrated judge ids that must NOT be shadowed
        reserved = {"helpfulness", "tone_professional", "faithfulness_judge"}
        assert reserved.isdisjoint(set(jq.criterion_ids()))

    def test_build_rubric_is_valid_and_covers_all(self):
        r = jq.build_rubric()
        assert isinstance(r, Rubric)
        assert {c.criterion_id for c in r.criteria} == set(jq.criterion_ids())
        # subset selection
        r2 = jq.build_rubric("sub", metric_ids=["groundedness_judge", "coherence_judge"])
        assert [c.criterion_id for c in r2.criteria] == [
            "groundedness_judge", "coherence_judge"]

    def test_metrics_metadata(self):
        for m in jq.JUDGE_QUALITY_METRICS:
            assert m.provisional is True
            assert m.category in ("rag", "quality", "safety")
            assert m.rubric_source in (jq.SOURCE_ORIGINAL, jq.SOURCE_ADAPTED_APACHE)
            assert m.methodology


# --------------------------------------------------------------------------- #
# Scoring through the real harness with a mock judge
# --------------------------------------------------------------------------- #

class TestScoringWithMockJudge:
    @pytest.mark.parametrize("metric_id", jq.criterion_ids())
    def test_scores_on_all_scale_values(self, metric_id):
        crit = jq.get_criterion(metric_id)
        values = ALLOWED_SCORES[crit.scale]
        judge = make_judge([verdict(v) for v in values])
        for v in values:
            cs = judge.score_criterion(crit, make_trace(), make_tc())
            assert cs.score == v
            assert cs.scorer == "judge"
            assert cs.judge_rationale

    def test_one_call_per_criterion(self):
        crit = jq.get_criterion("groundedness_judge")
        judge = make_judge([verdict(1.0), verdict(0.0)])
        judge.score_criterion(crit, make_trace(), make_tc())
        judge.score_criterion(crit, make_trace(), make_tc())
        assert len(judge.client.requests) == 2

    def test_out_of_scale_value_rejected(self):
        # a three_point criterion must reject a stray binary-only judge blob only
        # when the value isn't in its allowed set; use a clearly invalid value.
        crit = jq.get_criterion("coherence_judge")
        judge = make_judge([verdict(0.7), verdict(3)])
        with pytest.raises(JudgeError):
            judge.score_criterion(crit, make_trace(), make_tc())

    def test_prompt_contains_anchors_and_task_input(self):
        crit = jq.get_criterion("helpfulness_judge")
        judge = make_judge([verdict(1.0)])
        judge.score_criterion(crit, make_trace(), make_tc())
        prompt = judge.client.requests[0]["messages"][0]["content"]
        assert crit.anchors["pass"] in prompt
        assert crit.anchors["fail"] in prompt
        assert "Eiffel Tower" in prompt  # task input echoed

    @pytest.mark.parametrize("metric_id", [
        m.id for m in jq.JUDGE_QUALITY_METRICS if m.needs_context])
    def test_rag_metrics_see_reference_context_in_prompt(self, metric_id):
        crit = jq.get_criterion(metric_id)
        judge = make_judge([verdict(1.0)])
        judge.score_criterion(crit, make_trace(), make_tc(with_context=True))
        prompt = judge.client.requests[0]["messages"][0]["content"]
        assert "completed in 1889" in prompt  # the context text reaches the judge


# --------------------------------------------------------------------------- #
# PROVISIONAL by default (Hard Rule 6)
# --------------------------------------------------------------------------- #

class TestProvisionalByDefault:
    def test_none_in_demonstrated_calibrated_judge(self):
        from ascore.scoring.judge_calibration import demonstrated_calibrated_judge
        assert set(jq.criterion_ids()).isdisjoint(demonstrated_calibrated_judge())

    def test_uncalibrated_criteria_flags_all_of_them(self):
        from ascore.scoring.corpus import uncalibrated_criteria
        ids = jq.criterion_ids()
        scorers = {i: "judge" for i in ids}
        uncal = uncalibrated_criteria(ids, scorers)
        assert set(ids) <= uncal  # every judge-quality criterion is provisional

    def test_scored_criterion_marked_uncalibrated_in_engine(self):
        from ascore.scoring.engine import score_run
        crit = jq.get_criterion("conciseness_judge")
        rubric = Rubric(rubric_id="r", version=1, criteria=[crit])
        judge = make_judge([verdict(1.0)])
        rs = score_run(make_trace(), make_tc(), rubric, judge,
                       uncalibrated={crit.criterion_id})
        (cs,) = rs.criterion_scores
        assert cs.calibrated is False


# --------------------------------------------------------------------------- #
# Catalog registration
# --------------------------------------------------------------------------- #

class TestCatalogRegistration:
    def test_family_catalog_payload_fields(self):
        entries = {e["id"]: e for e in jq.catalog_payload()}
        assert set(entries) == set(jq.criterion_ids())
        for e in entries.values():
            assert e["scorer"] == "judge"
            assert e["provisional"] is True
            assert e["weight"] == 0.0
            assert e["rubric_source"] in (jq.SOURCE_ORIGINAL, jq.SOURCE_ADAPTED_APACHE)
            assert e["scale"] in ("binary", "three_point")

    def test_registered_in_main_catalog(self):
        payload = catalog_payload()
        ids = {e["id"] for e in payload}
        assert set(jq.criterion_ids()) <= ids
        # judge-quality entries are non-index (weight 0) and don't leak into
        # index weights.
        from ascore.metrics.catalog import index_weights
        assert set(jq.criterion_ids()).isdisjoint(index_weights())
