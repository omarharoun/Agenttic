"""Scoring engine — combines deterministic checks and the LLM judge into a
RunScore per trace (the scoreboard's assembly point).

``passed`` semantics: weighted mean of all criterion scores >= pass_threshold
(default 0.7, overridable per call). Scores for criteria flagged uncalibrated
are still computed but marked provisional (Hard Rule 6).
"""

from __future__ import annotations

from ascore.schema.rubric import Rubric
from ascore.schema.scorecard import CriterionScore, RunScore
from ascore.schema.testcase import TestCase
from ascore.schema.trace import Trace
from ascore.scoring.checks import run_check, validate_rubric_checks
from ascore.scoring.fi_eval import validate_rubric_fi
from ascore.scoring.judge import LLMJudge

DEFAULT_PASS_THRESHOLD = 0.7

#: checks that inspect the trajectory; meaningless for black-box traces
TRAJECTORY_ONLY_CHECKS = frozenset({
    "required_tool_called", "forbidden_tool_not_called", "steps_under_limit",
})


def applicable_criteria(rubric: Rubric, visibility: str):
    """Black-box traces can only be scored on criteria that don't need
    trajectory data (Step 7). Glass-box traces get the full rubric."""
    if visibility == "glass_box":
        return list(rubric.criteria)
    kept = [
        c for c in rubric.criteria
        if "trajectory" not in c.tags and c.check_ref not in TRAJECTORY_ONLY_CHECKS
    ]
    if not kept:
        raise ValueError(
            f"rubric {rubric.rubric_id}: no criteria applicable to black_box traces"
        )
    return kept


def score_run(
    trace: Trace,
    tc: TestCase,
    rubric: Rubric,
    judge: LLMJudge | None = None,
    *,
    uncalibrated: frozenset[str] | set[str] = frozenset(),
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    fi_evaluator=None,
) -> RunScore:
    validate_rubric_checks(rubric)
    validate_rubric_fi(rubric)
    criteria = applicable_criteria(rubric, trace.visibility)
    has_judge_criteria = any(c.scorer == "judge" for c in criteria)
    if has_judge_criteria and judge is None:
        raise ValueError(
            f"rubric {rubric.rubric_id} has judge criteria but no judge provided"
        )
    has_fi_criteria = any(c.scorer == "fi" for c in criteria)
    if has_fi_criteria and fi_evaluator is None:
        raise ValueError(
            f"rubric {rubric.rubric_id} has fi criteria but no fi evaluator provided"
        )

    scores: list[CriterionScore] = []
    for criterion in criteria:
        if criterion.scorer == "code":
            value = run_check(criterion.check_ref, trace, tc)
            cs = CriterionScore(
                criterion_id=criterion.criterion_id, score=value, scorer="code"
            )
        elif criterion.scorer == "fi":
            cs = fi_evaluator.score_criterion(criterion, trace, tc)
        else:
            cs = judge.score_criterion(criterion, trace, tc)
        cs.calibrated = criterion.criterion_id not in uncalibrated
        scores.append(cs)

    total_weight = sum(rubric.weights[c.criterion_id] for c in criteria)
    weighted = sum(
        s.score * rubric.weights[s.criterion_id] for s in scores
    ) / total_weight

    return RunScore(
        trace_id=trace.trace_id,
        test_id=tc.test_id,
        criterion_scores=scores,
        passed=weighted >= pass_threshold,
        cost_usd=trace.total_cost_usd,
        scoring_cost_usd=sum(s.cost_usd for s in scores),
        latency_ms=trace.total_latency_ms,
        steps=trace.total_steps,
    )
