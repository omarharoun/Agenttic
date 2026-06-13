"""Future AGI `fi` scorer: discretization, rationale, load-time validation,
and partial-batch behavior when an fi metric errors. Fully offline — a fake
evaluate_fn stands in for fi.evals.evaluate (the real dep is never imported).
"""

from types import SimpleNamespace as NS

import pytest

from ascore.scoring.fi_eval import (
    FI_METRICS,
    FiError,
    FiEvaluator,
    UnknownFiMetricError,
    validate_rubric_fi,
)
from ascore.schema.rubric import Criterion, Rubric
from tests.test_judge_calibration import make_tc, make_trace


def fake_evaluate(score=1.0, passed=None, reason="ok"):
    def _fn(metric, **kwargs):
        _fn.calls.append((metric, kwargs))
        return NS(score=score, passed=passed, reason=reason)
    _fn.calls = []
    return _fn


def fi_criterion(scale="binary", metric="contains"):
    return Criterion(criterion_id="c", description="d", scorer="fi",
                     scale=scale, fi_metric=metric)


class TestDiscretization:
    def test_binary_uses_fi_passed_verdict(self):
        ev = FiEvaluator(threshold=0.5, evaluate_fn=fake_evaluate(0.2, passed=True))
        cs = ev.score_criterion(fi_criterion(), make_trace(), make_tc())
        assert cs.score == 1.0 and cs.scorer == "fi"  # passed wins over raw<thr

    def test_binary_threshold_when_no_passed(self):
        below = FiEvaluator(threshold=0.5, evaluate_fn=fake_evaluate(0.4, passed=None))
        above = FiEvaluator(threshold=0.5, evaluate_fn=fake_evaluate(0.6, passed=None))
        assert below.score_criterion(fi_criterion(), make_trace(), make_tc()).score == 0.0
        assert above.score_criterion(fi_criterion(), make_trace(), make_tc()).score == 1.0

    def test_three_point_buckets(self):
        crit = fi_criterion(scale="three_point")
        for raw, expected in [(0.9, 1.0), (0.4, 0.5), (0.1, 0.0)]:
            ev = FiEvaluator(threshold=0.5, evaluate_fn=fake_evaluate(raw, passed=None))
            assert ev.score_criterion(crit, make_trace(), make_tc()).score == expected

    def test_rationale_carries_raw_and_reason(self):
        ev = FiEvaluator(evaluate_fn=fake_evaluate(0.73, passed=True,
                                                   reason="grounded in context"))
        cs = ev.score_criterion(fi_criterion(metric="faithfulness"),
                                make_trace(), make_tc())
        assert "fi:faithfulness" in cs.judge_rationale
        assert "0.73" in cs.judge_rationale and "grounded" in cs.judge_rationale

    def test_score_always_in_allowed_scale(self):
        # Hard Rule 3 — even a weird raw value never escapes {0,0.5,1}
        ev = FiEvaluator(evaluate_fn=fake_evaluate(0.999, passed=None))
        assert ev.score_criterion(fi_criterion("three_point"),
                                  make_trace(), make_tc()).score in (0.0, 0.5, 1.0)


class TestValidation:
    def test_validate_rubric_fi_rejects_unknown_metric(self):
        r = Rubric(rubric_id="r", criteria=[fi_criterion(metric="not_a_metric")])
        with pytest.raises(UnknownFiMetricError, match="not_a_metric"):
            validate_rubric_fi(r)

    def test_validate_passes_known_metric(self):
        r = Rubric(rubric_id="r", criteria=[fi_criterion(metric="contains")])
        validate_rubric_fi(r)  # no raise

    def test_criterion_requires_fi_metric(self):
        with pytest.raises(ValueError, match="fi criteria require fi_metric"):
            Criterion(criterion_id="c", description="d", scorer="fi", scale="binary")

    def test_local_metrics_are_marked(self):
        assert FI_METRICS["contains"].local is True
        assert FI_METRICS["faithfulness"].local is False


class TestErrorHandling:
    def test_evaluator_error_normalized_to_fi_error(self):
        def boom(metric, **kw):
            raise RuntimeError("network down")
        ev = FiEvaluator(evaluate_fn=boom)
        with pytest.raises(FiError, match="network down"):
            ev.score_criterion(fi_criterion(), make_trace(), make_tc())

    def test_missing_dependency_message(self):
        ev = FiEvaluator(evaluate_fn=None)  # forces the lazy import path
        # ai-evaluation isn't installed in the test env -> clear FiError
        with pytest.raises(FiError, match="ascore\\[fi\\]"):
            ev.score_criterion(fi_criterion(), make_trace(), make_tc())
