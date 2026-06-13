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


class TestSystemPromptOverride:
    def test_reference_adapter_uses_and_hashes_override(self):
        client = RoutingFakeClient()
        plain = ops.build_adapter(CFG, variant="reference", agent_id="x",
                                  client=client)
        triage = ops.build_adapter(CFG, variant="reference", agent_id="x",
                                   client=client,
                                   system_prompt="Reply ONLY the queue name.")
        assert triage.system_prompt == "Reply ONLY the queue name."
        assert triage.describe()["system_prompt"] == "Reply ONLY the queue name."
        # a prompt change is a config change — attributable across scorecards
        assert triage.config_hash() != plain.config_hash()

    def test_adapter_sends_override_to_the_model(self):
        class CaptureClient:
            def __init__(self):
                from types import SimpleNamespace as NS
                self.calls = []
                self.messages = NS(create=self._create)
            def _create(self, **kw):
                from types import SimpleNamespace as NS
                self.calls.append(kw)
                return NS(stop_reason="end_turn",
                          usage=NS(input_tokens=1, output_tokens=1),
                          content=[NS(type="text", text="billing")])
        cap = CaptureClient()
        adapter = ops.build_adapter(CFG, variant="reference", agent_id="x",
                                    client=cap, system_prompt="ONLY the queue.")
        adapter.run({"ticket": "refund"})
        assert cap.calls[0]["system"] == "ONLY the queue."


class TestPartialBatchScoring:
    def test_one_case_errors_others_still_scored(self, pilot_registry):
        reg, suite_id = pilot_registry

        class FlakyJudgeClient:
            """Raises on the WRONGCASE adversarial cases, scores 1 otherwise."""
            def __init__(self):
                from types import SimpleNamespace as NS
                import json
                self._json = json
                self.messages = NS(create=self._create)
            def _create(self, **kw):
                from types import SimpleNamespace as NS
                text = str(kw.get("messages"))
                if "WRONGCASE" in text or "wrongcase" in text:
                    raise RuntimeError("judge API timeout")
                return NS(content=[NS(type="text",
                          text=self._json.dumps({"score": 1, "rationale": "ok"}))])

        adapter = AnthropicSimpleAgent(model="agent-model",
                                       kb_path=PILOT / "kb.json",
                                       client=RoutingFakeClient(),
                                       agent_id="ref-agent")
        events = []
        sc = asyncio.run(ops.run_and_score_op(
            CFG, reg, adapter, suite_id,
            on_progress=lambda t, d: events.append((t, d)),
            judge_client=FlakyJudgeClient()))

        # the two WRONGCASE cases error during scoring; the batch survives
        assert set(sc.errored_test_ids) == {"triage-008", "triage-009"}
        scored = [r for r in sc.run_scores if r.scoring_error is None]
        assert len(scored) == 8
        # success rate is over the SCORED subset (8), not 10
        assert sc.task_success_rate == 1.0
        # errored cases are kept, marked, excluded from criterion means
        errored = [r for r in sc.run_scores if r.scoring_error]
        assert len(errored) == 2 and all("judge API timeout" in r.scoring_error
                                         for r in errored)
        assert "routing" in sc.per_criterion_means  # computed over scored only
        # cost still counts all 10 runs (the agent ran regardless)
        assert sc.mean_cost_usd > 0
        case_errors = [d for t, d in events if t == "case_error"]
        assert len(case_errors) == 2


class TestAggregatePartial:
    def _run(self, test_id, passed, error=None):
        from ascore.schema.scorecard import CriterionScore, RunScore
        crits = [] if error else [CriterionScore(
            criterion_id="x", score=1.0 if passed else 0.0, scorer="code")]
        return RunScore(trace_id=f"t-{test_id}", test_id=test_id,
                        criterion_scores=crits, passed=passed,
                        cost_usd=0.01, latency_ms=100.0, scoring_error=error)

    def test_rates_exclude_errored_cost_includes_all(self):
        from ascore.schema.scorecard import Scorecard
        runs = [self._run("a", True), self._run("b", False),
                self._run("c", False, error="JudgeError: boom")]
        sc = Scorecard.aggregate(
            scorecard_id="s", agent_id="ag", suite_id="su", suite_version=1,
            rubric_id="r", rubric_version=1, run_scores=runs,
            visibility_tier="glass_box")
        assert sc.errored_test_ids == ["c"]
        assert sc.task_success_rate == 0.5          # 1 of 2 scored
        assert sc.per_criterion_means == {"x": 0.5}  # over scored only
        assert sc.mean_cost_usd == pytest.approx(0.01)  # all 3 runs
        assert len(sc.run_scores) == 3              # errored kept

    def test_all_errored_does_not_crash(self):
        from ascore.schema.scorecard import Scorecard
        runs = [self._run("a", False, error="x"), self._run("b", False, error="y")]
        sc = Scorecard.aggregate(
            scorecard_id="s", agent_id="ag", suite_id="su", suite_version=1,
            rubric_id="r", rubric_version=1, run_scores=runs,
            visibility_tier="glass_box")
        assert sc.task_success_rate == 0.0 and sc.per_criterion_means == {}
        assert set(sc.errored_test_ids) == {"a", "b"}
