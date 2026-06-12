"""M1 end-to-end test (SPEC.md Hard Rule 8): the full pipeline — registry,
approval gate, harness, reference agent, code checks, judge, scorecard,
report, regression — on the hand-written pilot suite with mocked LLM calls.
"""

import asyncio
import json
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ascore.adapters.anthropic_simple import AnthropicSimpleAgent
from ascore.harness.runner import HarnessConfig, run_suite
from ascore.registry.sqlite_store import Registry
from ascore.reporting.scorecard_report import render_markdown
from ascore.schema.rubric import Rubric
from ascore.schema.scorecard import Scorecard
from ascore.schema.testcase import TestCase, TestSuite
from ascore.scoring.engine import score_run
from ascore.scoring.judge import LLMJudge

PILOT = Path(__file__).parent.parent / "examples" / "pilot_support_triage"


class RoutingFakeClient:
    """Mocked Claude for the reference agent. Stateless per call: first turn
    asks for the KB, the tool-result turn answers with a routing decision
    inferred from the ticket. Tickets containing WRONGCASE are deliberately
    misrouted to 'general' to exercise failures. Thread-safe."""

    def __init__(self):
        self.messages = NS(create=self._create)
        self._lock = threading.Lock()

    def _create(self, **kw):
        with self._lock:
            msgs = kw["messages"]
            ticket = json.loads(msgs[0]["content"])["ticket"]
            has_tool_result = any(
                isinstance(m.get("content"), list)
                and any(isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in m["content"])
                for m in msgs
            )
            if not has_tool_result:
                return NS(stop_reason="tool_use", usage=NS(input_tokens=200, output_tokens=30),
                          content=[NS(type="tool_use", name="lookup_kb",
                                      input={"key": "routing_rules"},
                                      id=f"tu_{uuid.uuid4().hex[:6]}")])
            t = ticket.lower()
            if "wrongcase" in t:
                queue = "general"          # deliberate misroute
            elif any(w in t for w in ("refund", "charge", "invoice")):
                queue = "billing"
            elif any(w in t for w in ("crash", "error", "bug", "password")):
                queue = "technical"
            else:
                queue = "general"
            return NS(stop_reason="end_turn", usage=NS(input_tokens=260, output_tokens=8),
                      content=[NS(type="text", text=queue)])


class ProfessionalToneJudgeClient:
    """Mocked judge: bare queue labels are professional (score 1)."""
    def __init__(self):
        self.messages = NS(create=lambda **kw: NS(content=[NS(
            type="text", text=json.dumps({"score": 1, "rationale": "Plain, neutral."}))]))


@pytest.fixture
def pilot():
    rubric = Rubric.model_validate_json((PILOT / "rubric.json").read_text())
    suite = TestSuite.model_validate_json((PILOT / "suite.json").read_text())
    cases = [TestCase.model_validate(c)
             for c in json.loads((PILOT / "cases.json").read_text())]
    return rubric, suite, cases


def test_full_pipeline_end_to_end(pilot, tmp_path):
    rubric, suite, cases = pilot
    reg = Registry(tmp_path / "e2e.db")
    reg.save_rubric(rubric)
    reg.save_suite(suite, cases)

    # human gate
    reg.approve_suite(suite.suite_id, suite.version)
    suite, cases = reg.get_suite(suite.suite_id)
    assert len(cases) == 10

    # harness run on the reference agent with the mocked model
    agent = AnthropicSimpleAgent(model="agent-model", kb_path=PILOT / "kb.json",
                                 client=RoutingFakeClient(), agent_id="ref-agent")
    traces = asyncio.run(run_suite(agent, suite, cases, reg,
                                   HarnessConfig(max_parallel=5, timeout_seconds=10)))
    assert len(reg.traces("ref-agent", mode="batch")) == 10
    assert all(any(s.kind == "tool_call" and s.name == "lookup_kb" for s in t.spans)
               for t in traces)

    # scoring: code checks + mocked judge
    judge = LLMJudge(model="judge-model", agent_model="agent-model",
                     client=ProfessionalToneJudgeClient())
    runs = [score_run(t, c, rubric, judge) for t, c in zip(traces, cases)]
    sc = Scorecard.aggregate(
        scorecard_id="sc-e2e-1", agent_id="ref-agent",
        suite_id=suite.suite_id, suite_version=suite.version,
        rubric_id=rubric.rubric_id, rubric_version=rubric.version,
        run_scores=runs, visibility_tier="glass_box")
    reg.save_scorecard(sc)

    # the two WRONGCASE adversarial cases fail; the other eight pass
    assert sc.task_success_rate == pytest.approx(0.8)
    failed = {r.test_id for r in runs if not r.passed}
    assert failed == {"triage-008", "triage-009"}
    assert sc.per_criterion_means["routing"] == pytest.approx(0.8)
    assert sc.per_criterion_means["used_kb"] == 1.0
    assert sc.mean_cost_usd > 0

    # report renders client-ready
    md = render_markdown(sc, rubric)
    assert "Executive summary" in md and "80%" in md
    assert "Recommendations" in md and "routing" in md

    # regression: second identical run diffs as unchanged
    traces2 = asyncio.run(run_suite(agent, suite, cases, reg))
    runs2 = [score_run(t, c, rubric, judge) for t, c in zip(traces2, cases)]
    sc2 = Scorecard.aggregate(
        scorecard_id="sc-e2e-2", agent_id="ref-agent",
        suite_id=suite.suite_id, suite_version=suite.version,
        rubric_id=rubric.rubric_id, rubric_version=rubric.version,
        run_scores=runs2, visibility_tier="glass_box")
    reg.save_scorecard(sc2)
    assert reg.suites_scored_for("ref-agent") == [suite.suite_id]
    md2 = render_markdown(sc2, rubric, previous=reg.scorecards_for("ref-agent")[0])
    assert "unchanged" in md2
