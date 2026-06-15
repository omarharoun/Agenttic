"""Spend ceilings: pre-run estimate gate + runtime per-run abort."""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from ascore.adapters.base import AgentAdapter
from ascore.budget import BudgetExceededError, RunBudget, check_pre_run
from ascore.harness.runner import HarnessConfig, run_suite
from ascore.registry.sqlite_store import Registry
from ascore.schema.testcase import TestCase, TestSuite
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace


class TestPreRunGate:
    def test_raises_over_run_cap(self, tmp_path):
        reg = Registry(tmp_path / "b.db")
        cfg = {"budget": {"max_run_cost_usd": 1.0}}
        with pytest.raises(BudgetExceededError):
            check_pre_run(cfg, reg, projected_usd=2.5)

    def test_warn_only_does_not_raise(self, tmp_path):
        reg = Registry(tmp_path / "b.db")
        cfg = {"budget": {"max_run_cost_usd": 1.0, "warn_only": True}}
        warnings = check_pre_run(cfg, reg, projected_usd=2.5)
        assert warnings and "exceeds per-run cap" in warnings[0]

    def test_daily_cap_counts_prior_spend(self, tmp_path):
        reg = Registry(tmp_path / "b.db")
        reg.record_spend("m", 0.8)
        cfg = {"budget": {"max_daily_cost_usd": 1.0}}
        # 0.8 already spent + 0.3 projected > 1.0
        with pytest.raises(BudgetExceededError):
            check_pre_run(cfg, reg, projected_usd=0.3)
        # 0.8 + 0.1 <= 1.0 is fine
        assert check_pre_run(cfg, reg, projected_usd=0.1) == []

    def test_no_caps_is_noop(self, tmp_path):
        reg = Registry(tmp_path / "b.db")
        assert check_pre_run({"budget": {}}, reg, projected_usd=999) == []


class TestGateInOpsPath:
    def test_run_and_score_blocked_by_tiny_cap(self, tmp_path):
        from ascore import ops
        from tests.test_e2e_pipeline import RoutingFakeClient
        from tests.test_executor import load_pilot

        reg = Registry(tmp_path / "b.db")
        load_pilot(reg)
        cfg = {
            "models": {"agent_default": "agent-model",
                       "judge_strong": "judge-model", "judge_light": "l"},
            "harness": {"timeout_seconds": 10, "max_parallel": 5,
                        "transport_retries": 1, "max_steps": 10},
            "scoring": {"calibration_threshold": 0.8},
            "budget": {"max_run_cost_usd": 0.00001},  # any real run exceeds this
        }
        adapter = ops.build_adapter(cfg, variant="reference", agent_id="ref-agent",
                                    client=RoutingFakeClient())
        with pytest.raises(BudgetExceededError):
            asyncio.run(ops.run_and_score_op(
                cfg, reg, adapter, "pilot-support-triage"))


class _CostingAgent(AgentAdapter):
    """Fake glass-box agent: every run costs a fixed amount."""
    visibility = "glass_box"

    def __init__(self, cost):
        self.agent_id = "coster"
        self.cost = cost

    def describe(self):
        return {"adapter": "coster", "cost": self.cost}

    def run(self, test_input, *, test_case_id=None):
        now = datetime.now(timezone.utc)
        return Trace(
            trace_id=uuid.uuid4().hex, agent_id=self.agent_id,
            agent_config_hash=self.config_hash(), test_case_id=test_case_id,
            spans=[Span(span_id="s", kind="final_output", name="f",
                        start_time=now, end_time=now)],
            visibility="glass_box", final_output="ok",
            total_cost_usd=self.cost, total_latency_ms=1.0, total_steps=1,
            schema_version=SCHEMA_VERSION)


class TestRuntimeAbort:
    def test_remaining_cases_abort_when_cap_hit(self, tmp_path):
        reg = Registry(tmp_path / "b.db")
        suite = TestSuite(suite_id="s", business_context="x", approved=True,
                          test_ids=[f"c{i}" for i in range(5)])
        cases = [TestCase(test_id=f"c{i}", suite_id="s", task_description="t",
                          input={}, rubric_id="r") for i in range(5)]
        budget = RunBudget(max_run_usd=0.25)  # cap after ~3 runs at $0.10
        traces = asyncio.run(run_suite(
            _CostingAgent(0.10), suite, cases, reg,
            HarnessConfig(max_parallel=1, timeout_seconds=5),
            budget=budget))
        aborted = [t for t in traces
                   if t.final_output == "HARNESS_FAILURE:budget_exceeded"]
        ran = [t for t in traces if t.final_output == "ok"]
        assert len(ran) == 3 and len(aborted) == 2   # 0.10*3 = 0.30 >= 0.25
        # every case still produced a persisted trace (none dropped)
        assert len(traces) == 5
