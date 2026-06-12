"""Step 9 acceptance tests (SPEC.md):
- Synthetic drift test: degraded outputs in a stream of fake traces; drift
  fires within the configured window
- Live scores never mix into batch scorecards (separate tables/queries)
"""

import json
import random
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from ascore.live.monitor import LiveMonitor
from ascore.registry.sqlite_store import Registry
from ascore.schema.rubric import Criterion
from ascore.schema.scorecard import CriterionScore, RunScore, Scorecard
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.scoring.judge import LLMJudge

NOW = datetime(2026, 6, 11, tzinfo=timezone.utc)

LIVE_CRIT = Criterion(
    criterion_id="helpful", description="Response addresses the user's request",
    scorer="judge", scale="binary", tags=["live"],
    anchors={"pass": "Direct, correct answer.", "fail": "Off-topic or empty."},
)


def live_trace(quality="good", agent="prod-agent"):
    return Trace(trace_id=uuid.uuid4().hex, agent_id=agent, agent_config_hash="h",
                 test_case_id=None,  # production traffic
                 spans=[Span(span_id="s", kind="final_output", name="f",
                             start_time=NOW, end_time=NOW)],
                 visibility="black_box",
                 final_output="Here is your answer." if quality == "good" else "",
                 schema_version=SCHEMA_VERSION)


class QualityAwareJudgeClient:
    """Lightweight judge stand-in: empty outputs score 0, others 1."""
    def __init__(self):
        self.messages = NS(create=self._create)
    def _create(self, **kw):
        prompt = kw["messages"][0]["content"]
        good = "Here is your answer." in prompt
        return NS(content=[NS(type="text", text=json.dumps(
            {"score": 1 if good else 0, "rationale": "auto"}))])


@pytest.fixture
def monitor(tmp_path):
    reg = Registry(tmp_path / "db.sqlite")
    judge = LLMJudge(model="judge-light", agent_model="prod-model",
                     client=QualityAwareJudgeClient())
    return LiveMonitor(registry=reg, judge=judge, live_criteria=[LIVE_CRIT],
                       sample_rate=1.0, drift_threshold=0.15, window=20,
                       rng=random.Random(7))


def baseline(mean=0.95):
    rs = RunScore(trace_id="t", test_id="tc", passed=True,
                  criterion_scores=[CriterionScore(criterion_id="helpful",
                                                   score=1.0, scorer="judge")])
    sc = Scorecard.aggregate(scorecard_id="base", agent_id="prod-agent",
                             suite_id="s", suite_version=1, rubric_id="r",
                             rubric_version=1, run_scores=[rs],
                             visibility_tier="black_box")
    sc.per_criterion_means = {"helpful": mean}
    return sc


class TestDriftDetection:
    def test_synthetic_degradation_fires_within_window(self, monitor):
        for _ in range(30):
            monitor.ingest(live_trace("good"))
        healthy = monitor.status("prod-agent", baseline())
        assert not healthy.drift_detected

        for _ in range(20):                      # one full window of bad traffic
            monitor.ingest(live_trace("bad"))
        degraded = monitor.status("prod-agent", baseline())
        assert degraded.drift_detected and degraded.drifted == ["helpful"]
        assert degraded.per_criterion_mean["helpful"] == 0.0
        reqs = monitor.registry.reeval_requests("prod-agent")
        assert len(reqs) == 1 and "re-evaluation" in reqs[0]

    def test_sampling_rate_respected(self, tmp_path):
        reg = Registry(tmp_path / "s.sqlite")
        judge = LLMJudge(model="j", agent_model="a",
                         client=QualityAwareJudgeClient())
        m = LiveMonitor(registry=reg, judge=judge, live_criteria=[LIVE_CRIT],
                        sample_rate=0.2, rng=random.Random(42))
        scored = sum(m.ingest(live_trace()) for _ in range(200))
        assert len(reg.traces("prod-agent", mode="live")) == 200  # all stored
        assert 20 <= scored <= 60                                  # ~20% judged


class TestSeparation:
    def test_batch_trace_rejected_by_live_path(self, monitor):
        batch = live_trace()
        batch.test_case_id = "tc-1"
        with pytest.raises(ValueError, match="batch"):
            monitor.ingest(batch)

    def test_live_data_never_in_batch_queries(self, monitor):
        for _ in range(5):
            monitor.ingest(live_trace())
        reg = monitor.registry
        assert reg.traces("prod-agent", mode="batch") == []
        assert reg.scorecards_for("prod-agent") == []   # no scorecards created

    def test_live_criteria_must_be_tagged_judge(self, monitor):
        untagged = Criterion(criterion_id="x", description="d", scorer="judge",
                             scale="binary", anchors={"pass": "p", "fail": "f"})
        with pytest.raises(ValueError, match="tagged 'live'"):
            LiveMonitor(registry=monitor.registry, judge=monitor.judge,
                        live_criteria=[untagged])
