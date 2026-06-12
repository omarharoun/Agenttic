"""Step 6 acceptance tests (SPEC.md):
- Re-running an old suite version reproduces identical test inputs
- Regression support: suites an agent was scored on are retrievable for re-run + diff
Plus: append-only versioning, approval gate persistence, live/batch trace separation.
"""

import uuid
from datetime import datetime, timezone

import pytest

from ascore.registry.sqlite_store import (
    DuplicateVersionError, NotFoundError, Registry,
)
from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.scorecard import CriterionScore, RunScore, Scorecard
from ascore.schema.testcase import TestCase, TestSuite
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace

NOW = datetime(2026, 6, 11, tzinfo=timezone.utc)


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "test.db")


def cases(version, n=3, suite_id="s-1", marker=""):
    return [TestCase(test_id=f"tc-{i}", suite_id=suite_id, version=version,
                     task_description="t", input={"q": f"{marker}q{i}"},
                     rubric_id="r-1") for i in range(n)]


def suite(version, cs, approved=False, suite_id="s-1"):
    return TestSuite(suite_id=suite_id, version=version, business_context="ctx",
                     test_ids=[c.test_id for c in cs], approved=approved)


def trace(agent="a-1"):
    now = NOW
    return Trace(trace_id=uuid.uuid4().hex, agent_id=agent, agent_config_hash="h",
                 spans=[Span(span_id="s1", kind="final_output", name="f",
                             start_time=now, end_time=now)],
                 visibility="glass_box", final_output="ok",
                 schema_version=SCHEMA_VERSION)


def scorecard(agent="a-1", suite_id="s-1", suite_version=1, sid=None):
    rs = RunScore(trace_id="t", test_id="tc-0", passed=True,
                  criterion_scores=[CriterionScore(criterion_id="c", score=1.0,
                                                   scorer="code")])
    return Scorecard.aggregate(scorecard_id=sid or uuid.uuid4().hex, agent_id=agent,
                               suite_id=suite_id, suite_version=suite_version,
                               rubric_id="r-1", rubric_version=1, run_scores=[rs],
                               visibility_tier="glass_box")


class TestVersionReproducibility:
    def test_old_version_reproduces_identical_inputs(self, reg):
        v1 = cases(1, marker="OLD-")
        reg.save_suite(suite(1, v1), v1)
        v2 = cases(2, marker="NEW-")
        reg.save_suite(suite(2, v2), v2)

        _, got_v1 = reg.get_suite("s-1", version=1)
        assert got_v1 == v1                       # byte-identical inputs
        latest_suite, got_latest = reg.get_suite("s-1")
        assert latest_suite.version == 2
        assert all(c.input["q"].startswith("NEW-") for c in got_latest)

    def test_same_version_twice_rejected(self, reg):
        cs = cases(1)
        reg.save_suite(suite(1, cs), cs)
        with pytest.raises(DuplicateVersionError):
            reg.save_suite(suite(1, cs), cs)

    def test_rubric_versioning(self, reg):
        crit = Criterion(criterion_id="c", description="d", scorer="code",
                         scale="binary", check_ref="valid_json_output")
        reg.save_rubric(Rubric(rubric_id="r-1", version=1, criteria=[crit]))
        with pytest.raises(DuplicateVersionError):
            reg.save_rubric(Rubric(rubric_id="r-1", version=1, criteria=[crit]))
        reg.save_rubric(Rubric(rubric_id="r-1", version=2, criteria=[crit]))
        assert reg.get_rubric("r-1").version == 2
        assert reg.get_rubric("r-1", version=1).version == 1


class TestApprovalGate:
    def test_approval_persists_and_is_version_scoped(self, reg):
        cs = cases(1)
        reg.save_suite(suite(1, cs), cs)
        assert reg.get_suite("s-1", 1)[0].approved is False
        reg.approve_suite("s-1", 1)
        assert reg.get_suite("s-1", 1)[0].approved is True

    def test_approving_missing_suite_raises(self, reg):
        with pytest.raises(NotFoundError):
            reg.approve_suite("ghost", 1)


class TestRegressionSupport:
    def test_suites_scored_for_agent_and_diff_base(self, reg):
        reg.save_scorecard(scorecard(suite_id="s-1"))
        reg.save_scorecard(scorecard(suite_id="s-2"))
        reg.save_scorecard(scorecard(agent="other", suite_id="s-9"))
        assert sorted(reg.suites_scored_for("a-1")) == ["s-1", "s-2"]
        history = reg.scorecards_for("a-1", suite_id="s-1")
        assert len(history) == 1 and history[0].suite_id == "s-1"

    def test_scorecard_round_trip(self, reg):
        sc = scorecard(sid="sc-x")
        reg.save_scorecard(sc)
        assert reg.get_scorecard("sc-x") == sc


class TestTraceStorage:
    def test_harness_protocol_and_live_batch_separation(self, reg):
        reg.save_trace(trace())                 # default batch (harness path)
        reg.save_trace(trace(), mode="live")
        reg.save_trace(trace(), mode="live")
        assert len(reg.traces("a-1", mode="batch")) == 1
        assert len(reg.traces("a-1", mode="live")) == 2

    def test_live_scores_and_reeval(self, reg):
        reg.save_live_scores("a-1", "tr-1", {"tone": 1.0, "safe": 0.0})
        reg.save_live_scores("a-1", "tr-2", {"tone": 0.5})
        assert reg.live_scores("a-1", "tone", last_n=10) == [0.5, 1.0]
        reg.save_reeval_request("a-1", "tone drifted 0.4 below baseline")
        assert "drifted" in reg.reeval_requests("a-1")[0]
