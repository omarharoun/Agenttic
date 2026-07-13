"""Step 1 acceptance tests (SPEC.md):
- All models round-trip model_dump_json -> model_validate_json
- schema_version present on Trace
- Validation failures raise (judge criterion without anchors, etc.)
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def make_span(i: int = 1, kind: str = "llm_call") -> Span:
    return Span(
        span_id=f"sp-{i}",
        kind=kind,
        name="step",
        start_time=T0,
        end_time=T0 + timedelta(seconds=1),
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
    )


def make_trace(**kw) -> Trace:
    defaults = dict(
        trace_id="tr-1",
        agent_id="agent-ref",
        agent_config_hash="abc123",
        test_case_id="tc-1",
        spans=[make_span(1), make_span(2, "tool_call"), make_span(3, "final_output")],
        visibility="glass_box",
        final_output="42",
        total_cost_usd=0.003,
        total_latency_ms=1200.0,
        total_steps=3,
    )
    defaults.update(kw)
    return Trace(**defaults)


JUDGE_CRIT = Criterion(
    criterion_id="tone",
    description="Reply tone is professional and empathetic",
    scorer="judge",
    scale="three_point",
    anchors={"pass": "Calm, specific, apologizes once.", "fail": "Sarcastic or blames the user."},
)
CODE_CRIT = Criterion(
    criterion_id="routing",
    description="Ticket routed to the correct queue",
    scorer="code",
    scale="binary",
    check_ref="final_output_matches_expected",
)


class TestRoundTrips:
    @pytest.mark.parametrize(
        "obj",
        [
            make_span(),
            make_trace(),
            TestCase(test_id="tc-1", suite_id="s-1", task_description="triage",
                     input={"ticket": "refund"}, rubric_id="r-1"),
            TestSuite(suite_id="s-1", business_context="support triage", test_ids=["tc-1"]),
            JUDGE_CRIT,
            CODE_CRIT,
            Rubric(rubric_id="r-1", criteria=[JUDGE_CRIT, CODE_CRIT]),
        ],
        ids=lambda o: type(o).__name__,
    )
    def test_json_round_trip(self, obj):
        restored = type(obj).model_validate_json(obj.model_dump_json())
        assert restored == obj

    def test_scorecard_round_trip(self):
        rs = RunScore(
            trace_id="tr-1", test_id="tc-1", passed=True,
            criterion_scores=[CriterionScore(criterion_id="routing", score=1.0, scorer="code")],
            cost_usd=0.003, latency_ms=1200.0, steps=3,
        )
        sc = Scorecard.aggregate(
            scorecard_id="sc-1", agent_id="agent-ref", suite_id="s-1", suite_version=1,
            rubric_id="r-1", rubric_version=1, run_scores=[rs], visibility_tier="glass_box",
        )
        restored = Scorecard.model_validate_json(sc.model_dump_json())
        assert restored == sc
        assert restored.task_success_rate == 1.0


class TestSchemaVersion:
    def test_trace_carries_schema_version(self):
        assert make_trace().schema_version == SCHEMA_VERSION

    def test_bump_rule_documented(self):
        import agenttic.schema.trace as m
        assert "MAJOR" in m.__doc__ and "MINOR" in m.__doc__


class TestCriterionNoneCoercion:
    """Regression: an explicit null for anchors/tags (LLM output or an older
    record) must coerce to empty, not crash with a dict/list type error.
    default_factory only fills a MISSING key, so None needs a before-validator."""

    def test_anchors_none_coerced_to_empty_dict(self):
        c = Criterion(criterion_id="x", description="d", scorer="code",
                      scale="binary", check_ref="f", anchors=None)
        assert c.anchors == {}

    def test_tags_none_coerced_to_empty_list(self):
        c = Criterion(criterion_id="x", description="d", scorer="code",
                      scale="binary", check_ref="f", tags=None)
        assert c.tags == []

    def test_judge_with_none_anchors_raises_hard_rule_2_not_type_error(self):
        # None -> {} then Hard Rule 2 rejects it with a clear message, NOT the
        # cryptic "Input should be a valid dictionary" the live bug produced.
        with pytest.raises(ValidationError) as ei:
            Criterion(criterion_id="x", description="d", scorer="judge",
                      scale="binary", anchors=None)
        assert "Hard Rule 2" in str(ei.value)
        assert "valid dictionary" not in str(ei.value)


class TestValidationFailures:
    def test_judge_criterion_without_anchors_raises(self):
        with pytest.raises(ValidationError, match="Hard Rule 2"):
            Criterion(criterion_id="x", description="d", scorer="judge",
                      scale="binary", anchors={})

    def test_judge_criterion_partial_anchors_raises(self):
        with pytest.raises(ValidationError, match="fail"):
            Criterion(criterion_id="x", description="d", scorer="judge",
                      scale="binary", anchors={"pass": "ok"})

    def test_code_criterion_without_check_ref_raises(self):
        with pytest.raises(ValidationError, match="check_ref"):
            Criterion(criterion_id="x", description="d", scorer="code", scale="binary")

    def test_wide_scale_rejected(self):
        with pytest.raises(ValidationError):
            Criterion(criterion_id="x", description="d", scorer="code",
                      scale="ten_point", check_ref="f")

    def test_score_outside_scale_rejected(self):
        with pytest.raises(ValidationError, match="Hard Rule 3"):
            CriterionScore(criterion_id="x", score=0.7, scorer="judge")

    def test_invalid_span_kind_rejected(self):
        with pytest.raises(ValidationError):
            make_span(kind="telepathy")

    def test_span_end_before_start_rejected(self):
        with pytest.raises(ValidationError, match="precedes"):
            Span(span_id="s", kind="llm_call", name="n",
                 start_time=T0, end_time=T0 - timedelta(seconds=1))

    def test_glass_box_trace_without_spans_rejected(self):
        with pytest.raises(ValidationError, match="must contain spans"):
            make_trace(spans=[])

    def test_black_box_trace_without_spans_allowed(self):
        tr = make_trace(visibility="black_box", spans=[])
        assert tr.visibility == "black_box"

    def test_duplicate_span_ids_rejected(self):
        with pytest.raises(ValidationError, match="duplicate span_id"):
            make_trace(spans=[make_span(1), make_span(1)])

    def test_rubric_weights_unknown_criterion_rejected(self):
        with pytest.raises(ValidationError, match="unknown criteria"):
            Rubric(rubric_id="r", criteria=[CODE_CRIT], weights={"ghost": 1.0})

    def test_rubric_default_weights_filled(self):
        r = Rubric(rubric_id="r", criteria=[JUDGE_CRIT, CODE_CRIT])
        assert r.weights == {"tone": 1.0, "routing": 1.0}

    def test_empty_scorecard_aggregation_rejected(self):
        with pytest.raises(ValueError, match="empty run set"):
            Scorecard.aggregate(
                scorecard_id="sc", agent_id="a", suite_id="s", suite_version=1,
                rubric_id="r", rubric_version=1, run_scores=[], visibility_tier="glass_box",
            )
