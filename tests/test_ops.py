"""M1 of the UI build: the shared ops layer (ascore/ops.py) and the harness
progress hook. Verifies CLI-parity of run_and_score_op against the e2e
expectations and that progress events fire in order with correct payloads.
"""

import asyncio
import json
from pathlib import Path

import pytest

from ascore import ops
from ascore.adapters.anthropic_simple import AnthropicSimpleAgent
from ascore.adapters.blackbox_http import BlackBoxHTTPAgent
from ascore.harness.runner import HarnessConfig, run_suite
from ascore.registry.sqlite_store import Registry
from ascore.registry.store import InMemoryTraceStore
from ascore.schema.rubric import Rubric
from ascore.schema.testcase import TestCase, TestSuite
from tests.test_e2e_pipeline import ProfessionalToneJudgeClient, RoutingFakeClient
from tests.test_harness import StubAdapter, make_cases, make_suite

PILOT = Path(__file__).parent.parent / "examples" / "pilot_support_triage"

CFG = {
    # no judge_executor -> plain strong judge (works with .messages fake)
    "models": {"agent_default": "agent-model", "judge_strong": "judge-model",
               "judge_light": "judge-light"},
    "harness": {"timeout_seconds": 10, "max_parallel": 5,
                "transport_retries": 1, "max_steps": 10},
    "scoring": {"calibration_threshold": 0.8},
    "paths": {"review_dir": "review/"},
}


@pytest.fixture
def pilot_registry(tmp_path):
    reg = Registry(tmp_path / "ops.db")
    reg.save_rubric(Rubric.model_validate_json((PILOT / "rubric.json").read_text()))
    suite = TestSuite.model_validate_json((PILOT / "suite.json").read_text())
    cases = [TestCase.model_validate(c)
             for c in json.loads((PILOT / "cases.json").read_text())]
    reg.save_suite(suite, cases)
    reg.approve_suite(suite.suite_id, suite.version)
    return reg, suite.suite_id


class TestRunAndScoreOp:
    def test_cli_parity_with_progress_events(self, pilot_registry):
        reg, suite_id = pilot_registry
        adapter = AnthropicSimpleAgent(model="agent-model",
                                       kb_path=PILOT / "kb.json",
                                       client=RoutingFakeClient(),
                                       agent_id="ref-agent")
        events = []
        sc = asyncio.run(ops.run_and_score_op(
            CFG, reg, adapter, suite_id,
            on_progress=lambda t, d: events.append((t, d)),
            judge_client=ProfessionalToneJudgeClient()))

        assert sc.task_success_rate == pytest.approx(0.8)
        assert reg.get_scorecard(sc.scorecard_id).suite_id == suite_id

        by_type = {}
        for t, d in events:
            by_type.setdefault(t, []).append(d)
        assert len(by_type["case_started"]) == 10
        assert len(by_type["case_finished"]) == 10
        assert len(by_type["case_scored"]) == 10
        assert all(d["total"] == 10 for _, d in events)
        assert all("trace_id" in d for d in by_type["case_finished"])
        scored_ids = {d["test_id"] for d in by_type["case_scored"]}
        assert scored_ids == {f"triage-{i:03d}" for i in range(10)}

    def test_report_op_renders(self, pilot_registry):
        reg, suite_id = pilot_registry
        adapter = AnthropicSimpleAgent(model="agent-model",
                                       kb_path=PILOT / "kb.json",
                                       client=RoutingFakeClient(),
                                       agent_id="ref-agent")
        sc = asyncio.run(ops.run_and_score_op(
            CFG, reg, adapter, suite_id,
            judge_client=ProfessionalToneJudgeClient()))
        md = ops.report_op(reg, sc.scorecard_id)
        assert "Executive summary" in md and "80%" in md


class TestBuildAdapter:
    def test_reference(self):
        a = ops.build_adapter(CFG, variant="reference", agent_id="x",
                              client=RoutingFakeClient())
        assert isinstance(a, AnthropicSimpleAgent)
        assert a.model == "agent-model"

    def test_blackbox(self):
        a = ops.build_adapter(CFG, variant="blackbox", agent_id="x",
                              url="http://h/run")
        assert isinstance(a, BlackBoxHTTPAgent)

    def test_blackbox_requires_url(self):
        with pytest.raises(ValueError, match="url"):
            ops.build_adapter(CFG, variant="blackbox", agent_id="x")

    def test_managed_requires_ids(self):
        with pytest.raises(ValueError, match="environment_id"):
            ops.build_adapter(CFG, variant="managed", agent_id="x",
                              managed_agent_id="agent_01")

    def test_agent_model_of_blackbox_never_collides(self):
        a = ops.build_adapter(CFG, variant="blackbox", agent_id="cx",
                              url="http://h/run")
        assert ops.agent_model_of(a) == "blackbox:cx"


class TestHarnessProgressHook:
    def test_event_order_and_default_none(self):
        cases, store = make_cases(3), InMemoryTraceStore()
        events = []
        asyncio.run(run_suite(StubAdapter(), make_suite(cases), cases, store,
                              HarnessConfig(max_parallel=1, timeout_seconds=5),
                              on_event=lambda t, d: events.append((t, d))))
        # max_parallel=1 -> strictly interleaved start/finish per case
        types = [t for t, _ in events]
        assert types == ["case_started", "case_finished"] * 3
        assert events[1][1]["ok"] is True
        # default path (no callback) unchanged
        asyncio.run(run_suite(StubAdapter(), make_suite(cases), cases, store))

    def test_failure_case_reports_not_ok(self):
        cases, store = make_cases(1), InMemoryTraceStore()
        adapter = StubAdapter(errors=[RuntimeError("agent bug")])
        events = []
        asyncio.run(run_suite(adapter, make_suite(cases), cases, store,
                              on_event=lambda t, d: events.append((t, d))))
        finished = [d for t, d in events if t == "case_finished"]
        assert finished[0]["ok"] is False
