"""pass^k must cost k INDEPENDENT executions of every case.

The defect these pin, and why it was invisible: ``metrics.runner.run_standard``
repeats a suite by calling ``ops.run_suite_op`` k times, ``run_suite_op``
hard-codes ``resume=True`` ("resilience is mandatory"), and the harness resume
map was keyed on ``test_case_id`` with no trial dimension. So trial 1 ran the
agent and trials 2..k read trial 1's persisted traces back and returned them
unchanged. The agent was invoked ONCE. Every trial saw byte-identical output.
``pass^k`` was therefore not a flakiness measurement at all — it was
arithmetically forced to equal ``pass@1`` for every agent, and every published
reliability number derived from it was an alias for the single-run pass rate.

It survived because nothing pinned the only fact that distinguishes the two:
**how many times the agent actually ran.** These tests pin it, in both
directions — k trials must cost k executions, AND resume must still rescue a
crashed run mid-suite, which is the reason resume is on in the first place.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from agenttic import ops
from agenttic.harness.runner import HarnessConfig, run_suite
from agenttic.metrics.runner import run_standard
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

SUITE_ID = "std-tool-use-v1"
CFG = {
    "models": {"agent_default": "agent-model", "judge_strong": "judge-model"},
    "harness": {"timeout_seconds": 10, "max_parallel": 4,
                "transport_retries": 0, "max_steps": 5},
}
RUBRIC = Rubric(rubric_id="r", criteria=[
    Criterion(criterion_id="tool_selection_accuracy", description="d",
              scorer="code", scale="binary",
              check_ref="final_output_matches_expected", anchors={})])


class CountingAgent:
    """Records every invocation and emits a DIFFERENT answer each time, so a
    replayed trace is distinguishable from a fresh run by inspection."""

    visibility = "glass_box"
    model = "agent-model"

    def __init__(self, agent_id: str = "counter", *, answers=None):
        self.agent_id = agent_id
        self.calls: list[str] = []
        self._answers = answers or {}

    def describe(self):
        return {"adapter": "counting"}

    def config_hash(self):
        return "counting-hash-v1"

    def run(self, test_input, *, test_case_id=None):
        n = sum(1 for c in self.calls if c == test_case_id)
        self.calls.append(test_case_id)
        answer = self._answers.get(test_case_id, ["ok"])
        now = datetime.now(timezone.utc)
        return Trace(
            trace_id=uuid.uuid4().hex, agent_id=self.agent_id,
            agent_config_hash=self.config_hash(), test_case_id=test_case_id,
            spans=[Span(span_id="s0", kind="tool_call", name="lookup_account",
                        start_time=now, end_time=now),
                   Span(span_id="s1", kind="final_output", name="final_output",
                        start_time=now, end_time=now)],
            visibility="glass_box",
            final_output=answer[min(n, len(answer) - 1)],
            total_cost_usd=0.01, total_latency_ms=5.0, total_steps=2,
            schema_version=SCHEMA_VERSION)


def _registry(tmp_path, n_cases: int):
    reg = Registry(tmp_path / "k.db")
    reg.save_rubric(RUBRIC)
    cases = [TestCase(test_id=f"c{i}", suite_id=SUITE_ID, task_description="t",
                      input={"q": i}, expected={"final_output": "ok"},
                      rubric_id="r") for i in range(n_cases)]
    suite = TestSuite(suite_id=SUITE_ID, business_context="b",
                      test_ids=[c.test_id for c in cases], approved=True)
    reg.save_suite(suite, cases)
    reg.approve_suite(SUITE_ID, suite.version)
    return reg, suite, cases


def _score_on_output(monkeypatch):
    """Score straight off the trace the harness returned: a case passes iff its
    final_output is "ok". Nothing else is faked — the harness, the registry and
    the resume path are all real, which is where the defect lived."""
    async def fake_score(cfg, reg, traces, cases, model, on_progress=None,
                         judge_client=None, fi_evaluate_fn=None):
        by_case = {c.test_id: c for c in cases}
        return [RunScore(
            trace_id=t.trace_id, test_id=t.test_case_id,
            passed=(t.final_output == "ok"), cost_usd=t.total_cost_usd,
            criterion_scores=[CriterionScore(
                criterion_id="tool_selection_accuracy",
                score=1.0 if t.final_output == "ok" else 0.0, scorer="code")])
            for t in traces if t.test_case_id in by_case]
    monkeypatch.setattr(ops, "score_op", fake_score)


# --------------------------------------------------------------------------- #
# 1. the root cause: k trials must cost k executions
# --------------------------------------------------------------------------- #

class TestKTrialsAreIndependentExecutions:
    def test_a_k_trial_run_invokes_the_adapter_n_cases_times_k(
            self, tmp_path, monkeypatch):
        """THE missing test. With resume keyed on test_case_id alone this was 3,
        not 9: trials 2 and 3 replayed trial 1's traces and never reached the
        agent."""
        reg, _suite, cases = _registry(tmp_path, 3)
        _score_on_output(monkeypatch)
        agent = CountingAgent()

        res = asyncio.run(run_standard(CFG, reg, agent, k=3,
                                       suite_ids=[SUITE_ID]))

        assert len(agent.calls) == len(cases) * 3 == 9
        assert sorted(agent.calls) == sorted([c.test_id for c in cases] * 3)
        assert res["k"] == 3 and res["n_cases"] == 3

    def test_every_trial_persists_its_own_trace(self, tmp_path, monkeypatch):
        """The database evidence the audit found: four k=3 runs of a 17-case
        suite had left 17 traces, not 204."""
        reg, _suite, _cases = _registry(tmp_path, 3)
        _score_on_output(monkeypatch)
        agent = CountingAgent()

        asyncio.run(run_standard(CFG, reg, agent, k=3, suite_ids=[SUITE_ID]))

        stored = reg.traces(agent.agent_id, mode="batch")
        assert len(stored) == 9
        assert len({t.trace_id for t in stored}) == 9      # nine distinct runs

    def test_a_flaky_agent_separates_pass_k_from_pass_at_1(
            self, tmp_path, monkeypatch):
        """The measurement the defect destroyed. c0 succeeds once then fails;
        c1 always succeeds. pass@1 sees two passes, pass^k sees one reliable
        case. Under the replay bug BOTH were 1.0, for every agent alive."""
        reg, _suite, _cases = _registry(tmp_path, 2)
        _score_on_output(monkeypatch)
        agent = CountingAgent(answers={"c0": ["ok", "wrong"], "c1": ["ok"]})

        res = asyncio.run(run_standard(CFG, reg, agent, k=3,
                                       suite_ids=[SUITE_ID]))

        assert res["pass_at_1"] == 1.0
        assert res["components"]["reliability_pass_k"] == 0.5
        assert res["pass_at_1"] != res["components"]["reliability_pass_k"]

    def test_the_reported_cost_matches_what_was_actually_spent(
            self, tmp_path, monkeypatch):
        """`k_runs_cost_usd` sums each trial's RunScore costs. Replaying trial 0's
        trace k times charged its cost k times while only one execution was ever
        paid for, so the published spend figure was inflated k-fold in the same
        breath that the reliability figure was. Both are now the real number."""
        reg, _suite, cases = _registry(tmp_path, 3)
        _score_on_output(monkeypatch)
        agent = CountingAgent()

        res = asyncio.run(run_standard(CFG, reg, agent, k=3,
                                       suite_ids=[SUITE_ID]))

        spent = sum(t.total_cost_usd
                    for t in reg.traces(agent.agent_id, mode="batch"))
        assert spent == pytest.approx(len(cases) * 3 * 0.01)
        assert res["k_runs_cost_usd"] == pytest.approx(spent)

    def test_k_equals_one_still_runs_each_case_once(self, tmp_path, monkeypatch):
        reg, _suite, cases = _registry(tmp_path, 3)
        _score_on_output(monkeypatch)
        agent = CountingAgent()
        asyncio.run(run_standard(CFG, reg, agent, k=1, suite_ids=[SUITE_ID]))
        assert len(agent.calls) == len(cases)


# --------------------------------------------------------------------------- #
# 2. the harness contract underneath it
# --------------------------------------------------------------------------- #

class TestTrialOrdinalResume:
    def _run(self, agent, suite, cases, reg, *, trial=0, on_event=None):
        return asyncio.run(run_suite(
            agent, suite, cases, reg,
            HarnessConfig(max_parallel=2, timeout_seconds=5, transport_retries=0),
            on_event=on_event, trial=trial))

    def test_trial_one_does_not_replay_trial_zero(self, tmp_path):
        reg, suite, cases = _registry(tmp_path, 2)
        agent = CountingAgent(answers={"c0": ["first", "second"],
                                       "c1": ["first", "second"]})
        t0 = self._run(agent, suite, cases, reg, trial=0)
        t1 = self._run(agent, suite, cases, reg, trial=1)
        assert [t.final_output for t in t0] == ["first", "first"]
        assert [t.final_output for t in t1] == ["second", "second"]
        assert len(agent.calls) == 4

    def test_re_running_the_same_trial_resumes_it(self, tmp_path):
        """Resume is per trial, not switched off: asking for trial 1 twice must
        not spend twice."""
        reg, suite, cases = _registry(tmp_path, 2)
        agent = CountingAgent(answers={"c0": ["a", "b", "c"], "c1": ["a", "b", "c"]})
        self._run(agent, suite, cases, reg, trial=0)
        first = self._run(agent, suite, cases, reg, trial=1)
        calls_after = len(agent.calls)
        again = self._run(agent, suite, cases, reg, trial=1)
        assert len(agent.calls) == calls_after           # nothing re-spent
        assert [t.trace_id for t in again] == [t.trace_id for t in first]

    def test_resume_still_rescues_a_trial_that_died_mid_suite(self, tmp_path):
        """The reason resume is mandatory, preserved. Trial 1 got one case done
        before the process died; the retry must re-run only the other two."""
        reg, suite, cases = _registry(tmp_path, 3)
        agent = CountingAgent()
        self._run(agent, suite, cases, reg, trial=0)          # trial 0 complete
        self._run(agent, suite, cases[:1], reg, trial=1)      # trial 1 dies after c0
        assert len(agent.calls) == 4

        events: list[str] = []
        self._run(agent, suite, cases, reg, trial=1,
                  on_event=lambda e, _d: events.append(e))
        assert events.count("case_resumed") == 1              # only c0 was done
        assert len(agent.calls) == 6                          # c1 and c2 re-run

    def test_a_failed_run_is_never_reused_as_a_trial(self, tmp_path):
        """Harness/upstream failures were already excluded from resume; the
        trial ordinal must not smuggle them back in as a completed trial."""
        reg, suite, cases = _registry(tmp_path, 1)
        agent = CountingAgent(answers={"c0": ["HARNESS_FAILURE:transport", "ok"]})
        t0 = self._run(agent, suite, cases, reg, trial=0)
        assert t0[0].final_output == "HARNESS_FAILURE:transport"
        t0b = self._run(agent, suite, cases, reg, trial=0)
        assert t0b[0].final_output == "ok"        # the failure was re-run, not reused
