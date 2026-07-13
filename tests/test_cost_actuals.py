"""Actual cost capture: judge token cost flows into RunScore/Scorecard."""

from types import SimpleNamespace as NS

from agenttic.schema.rubric import Criterion
from agenttic.schema.scorecard import RunScore, Scorecard
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.scoring.judge import LLMJudge

CFG = {"pricing": {"judge-model": {"input": 10.0, "output": 30.0},
                   "default": {"input": 3.0, "output": 15.0}}}


def _trace():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return Trace(trace_id="t1", agent_id="a", agent_config_hash="h",
                 spans=[Span(span_id="s", kind="final_output", name="f",
                             start_time=now, end_time=now)],
                 visibility="glass_box", final_output="ok",
                 schema_version=SCHEMA_VERSION)


class FakeJudgeClient:
    """Returns a fixed verdict with token usage so cost can be priced."""
    def __init__(self):
        self.messages = NS(create=self._c)
    def _c(self, **kw):
        return NS(stop_reason="end_turn",
                  usage=NS(input_tokens=1000, output_tokens=100),
                  content=[NS(type="text", text='{"score": 1, "rationale": "good"}')])


def test_judge_cost_priced_from_usage():
    judge = LLMJudge(model="judge-model", agent_model="agent-model",
                     client=FakeJudgeClient(), cfg=CFG)
    crit = Criterion(criterion_id="tone", description="d", scorer="judge",
                     scale="binary", anchors={"pass": "p", "fail": "f"})
    cs = judge.score_criterion(crit, _trace(),
                               TestCase(test_id="x", suite_id="s",
                                        task_description="t", rubric_id="r"))
    # (1000*10 + 100*30)/1e6 = 0.013
    assert cs.cost_usd == (1000 * 10 + 100 * 30) / 1_000_000


def test_judge_cost_zero_without_cfg():
    judge = LLMJudge(model="judge-model", agent_model="agent-model",
                     client=FakeJudgeClient())  # no cfg
    crit = Criterion(criterion_id="tone", description="d", scorer="judge",
                     scale="binary", anchors={"pass": "p", "fail": "f"})
    cs = judge.score_criterion(crit, _trace(),
                               TestCase(test_id="x", suite_id="s",
                                        task_description="t", rubric_id="r"))
    assert cs.cost_usd == 0.0


def test_scorecard_totals_execution_and_scoring_cost():
    from agenttic.schema.scorecard import CriterionScore
    runs = [
        RunScore(trace_id="t1", test_id="c1", passed=True, cost_usd=0.02,
                 scoring_cost_usd=0.013, criterion_scores=[
                     CriterionScore(criterion_id="x", score=1.0, scorer="judge",
                                    cost_usd=0.013)]),
        RunScore(trace_id="t2", test_id="c2", passed=False, cost_usd=0.04,
                 scoring_cost_usd=0.013, criterion_scores=[
                     CriterionScore(criterion_id="x", score=0.0, scorer="judge",
                                    cost_usd=0.013)]),
    ]
    sc = Scorecard.aggregate(
        scorecard_id="sc1", agent_id="a", suite_id="s", suite_version=1,
        rubric_id="r", rubric_version=1, run_scores=runs,
        visibility_tier="glass_box")
    assert sc.total_cost_usd == 0.06
    assert sc.total_scoring_cost_usd == 0.026
    assert sc.mean_cost_usd == 0.03
