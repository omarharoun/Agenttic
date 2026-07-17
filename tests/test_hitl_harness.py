"""SPEC-2 Step 12 acceptance tests — HITL harness: escalation + confidence-
gated autonomy.

Covers each acceptance criterion:
- a scripted HumanChannel resolves an escalation: the run completes WITH the
  human's guidance applied, and both the escalation span and the
  HumanFeedback(source="escalation") are persisted;
- with NO channel, the run is persisted (never dropped) as
  final_output=="ESCALATED_UNRESOLVED", escalated=True;
- the ``escalated_appropriately`` check scores 1.0 when a should_escalate case
  defers and 0.0 when it answers autonomously (and the inverse for an untagged
  case);
- a ``human_required`` tool in the reference agent's policy triggers escalation.

All LLM/human calls are scripted fakes (Hard Rule 8): no network.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from agenttic.adapters.anthropic_simple import AnthropicSimpleAgent
from agenttic.adapters.base import EscalationRequired
from agenttic.harness.runner import run_suite
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.scoring.checks import run_check


# --------------------------------------------------------------------------- #
# Fakes.
# --------------------------------------------------------------------------- #


def _now():
    return datetime.now(timezone.utc)


def _span(kind="llm_call", name="s"):
    now = _now()
    return Span(span_id=uuid.uuid4().hex[:12], kind=kind, name=name,
                start_time=now, end_time=now)


class ScriptedHuman:
    """A HumanChannel stub that returns a fixed decision and records calls."""

    def __init__(self, decision: str = "Approved: proceed with the refund."):
        self.decision = decision
        self.calls: list[tuple[str, dict]] = []

    def respond(self, question: str, context: dict) -> str:
        self.calls.append((question, context))
        return self.decision


class EscalatingAdapter:
    """Escalates on the FIRST invocation; on the re-invocation (once the harness
    has injected ``human_guidance``) it finishes normally, marking the guidance
    it received so the test can assert it was applied."""

    agent_id = "escalating-agent"
    visibility = "glass_box"

    def __init__(self):
        self.inputs: list[dict] = []

    def describe(self):
        return {"adapter": "escalating"}

    def config_hash(self):
        return "escalhash"

    def run(self, test_input, *, test_case_id=None):
        self.inputs.append(dict(test_input))
        if "human_guidance" not in test_input:
            raise EscalationRequired(
                "Authorize issue_refund?",
                context={"tool": "issue_refund", "test_case_id": test_case_id},
                partial_trace_spans=[_span("tool_call", "lookup_kb")],
            )
        guidance = test_input["human_guidance"]
        now = _now()
        return Trace(
            trace_id=uuid.uuid4().hex, agent_id=self.agent_id,
            agent_config_hash=self.config_hash(), test_case_id=test_case_id,
            spans=[Span(span_id=uuid.uuid4().hex[:12], kind="final_output",
                        name="final_output", start_time=now, end_time=now,
                        output={"text": guidance})],
            visibility="glass_box", final_output=f"DONE:{guidance}",
            schema_version=SCHEMA_VERSION,
        )


def make_case(test_id="tc-1", suite_id="s-1", tags=None):
    return TestCase(test_id=test_id, suite_id=suite_id, task_description="t",
                    input={"q": "refund please"}, tags=tags or [], rubric_id="r-1")


def make_suite(cases, suite_id="s-1"):
    return TestSuite(suite_id=suite_id, business_context="ctx",
                     test_ids=[c.test_id for c in cases], approved=True)


# --------------------------------------------------------------------------- #
# 1. Scripted HumanChannel resolves the escalation.
# --------------------------------------------------------------------------- #


class TestEscalationResolvedByHuman:
    def test_human_guidance_applied_and_persisted(self, tmp_path):
        reg = Registry(tmp_path / "hitl.db")
        adapter = EscalatingAdapter()
        cases = [make_case()]
        human = ScriptedHuman("Approved: proceed with the refund.")

        traces = asyncio.run(run_suite(
            adapter, make_suite(cases), cases, reg, human=human))

        assert len(traces) == 1
        trace = traces[0]
        # the run completed WITH the human guidance folded in
        assert trace.escalated is True
        assert trace.final_output == "DONE:Approved: proceed with the refund."
        # the adapter was re-invoked with the guidance in test_input
        assert len(adapter.inputs) == 2
        assert "human_guidance" not in adapter.inputs[0]
        assert adapter.inputs[1]["human_guidance"] == \
            "Approved: proceed with the refund."
        # the human was consulted exactly once with the escalation question
        assert len(human.calls) == 1
        assert human.calls[0][0] == "Authorize issue_refund?"

        # an escalation span is present (prepended to the resolved run)
        esc = [s for s in trace.spans if s.kind == "escalation"]
        assert len(esc) == 1
        assert esc[0].input["question"] == "Authorize issue_refund?"

        # the human decision was persisted as HumanFeedback(source="escalation")
        fb = reg.feedback_for_trace(trace.trace_id)
        assert len(fb) == 1
        assert fb[0].source == "escalation"
        assert fb[0].kind == "escalation_decision"
        assert fb[0].rationale == "Approved: proceed with the refund."


# --------------------------------------------------------------------------- #
# 2. No channel: persisted as ESCALATED_UNRESOLVED, not dropped.
# --------------------------------------------------------------------------- #


class TestEscalationNoChannel:
    def test_unresolved_persisted_not_dropped(self, tmp_path):
        reg = Registry(tmp_path / "hitl2.db")
        adapter = EscalatingAdapter()
        cases = [make_case()]

        traces = asyncio.run(run_suite(
            adapter, make_suite(cases), cases, reg, human=None))

        assert len(traces) == 1
        trace = traces[0]
        assert trace.final_output == "ESCALATED_UNRESOLVED"
        assert trace.escalated is True
        # the run was NOT dropped — it is in the store
        persisted = reg.traces(adapter.agent_id, mode="batch")
        assert any(t.trace_id == trace.trace_id for t in persisted)
        # the partial work + the escalation span are preserved
        kinds = [s.kind for s in trace.spans]
        assert "escalation" in kinds
        assert "tool_call" in kinds  # the partial span carried on the exception
        # the adapter was invoked once (no re-invoke without a channel)
        assert len(adapter.inputs) == 1
        # no human feedback recorded (there was no human)
        assert reg.feedback_for_trace(trace.trace_id) == []


# --------------------------------------------------------------------------- #
# 3. escalated_appropriately scoring.
# --------------------------------------------------------------------------- #


def _trace(escalated: bool):
    now = _now()
    return Trace(
        trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
        test_case_id="c", visibility="glass_box", final_output="x",
        spans=[Span(span_id="s1", kind="final_output", name="f",
                    start_time=now, end_time=now)],
        escalated=escalated, schema_version=SCHEMA_VERSION,
    )


class TestEscalatedAppropriatelyCheck:
    def test_tagged_case(self):
        tc = make_case(tags=["should_escalate"])
        # deferred (escalated) -> correct
        assert run_check("escalated_appropriately", _trace(True), tc) == 1.0
        # answered autonomously when it should have deferred -> wrong
        assert run_check("escalated_appropriately", _trace(False), tc) == 0.0

    def test_untagged_case(self):
        tc = make_case(tags=["happy_path"])
        # acted autonomously -> correct
        assert run_check("escalated_appropriately", _trace(False), tc) == 1.0
        # deferred when it should have just answered -> wrong
        assert run_check("escalated_appropriately", _trace(True), tc) == 0.0


# --------------------------------------------------------------------------- #
# 4. A human_required tool triggers escalation in the reference agent.
# --------------------------------------------------------------------------- #


class FakeAnthropicClient:
    """Replays scripted Anthropic responses; records requests."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.messages = NS(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def _tool_use(name, args, id_="tu_1"):
    return NS(type="tool_use", name=name, input=args, id=id_)


def _text(t):
    return NS(type="text", text=t)


def _usage():
    return NS(input_tokens=10, output_tokens=5)


@pytest.fixture
def kb_file(tmp_path):
    p = tmp_path / "kb.json"
    p.write_text(json.dumps({"refund_policy": "30 days"}))
    return p


class TestReferenceAgentAutonomyPolicy:
    def test_human_required_tool_escalates(self, kb_file):
        # the model asks to call `calculator`, which the policy marks
        # human_required -> the agent must escalate instead of executing it.
        client = FakeAnthropicClient([
            NS(stop_reason="tool_use", usage=_usage(),
               content=[_tool_use("calculator", {"expression": "2+2"})]),
        ])
        agent = AnthropicSimpleAgent(
            model="m", kb_path=kb_file, client=client,
            autonomy_policy={"default": "auto",
                             "overrides": {"calculator": "human_required"}},
        )
        with pytest.raises(EscalationRequired) as ei:
            agent.run({"q": "what is 2+2"}, test_case_id="tc-x")
        exc = ei.value
        assert exc.question == "Authorize calculator?"
        assert exc.context["tool"] == "calculator"
        # the LLM-call span produced before the escalation is carried along
        assert any(s.kind == "llm_call" for s in exc.partial_trace_spans)

    def test_auto_tool_runs_without_escalation(self, kb_file):
        # default `auto`: the calculator runs and the agent finishes normally.
        client = FakeAnthropicClient([
            NS(stop_reason="tool_use", usage=_usage(),
               content=[_tool_use("calculator", {"expression": "2+2"})]),
            NS(stop_reason="end_turn", usage=_usage(),
               content=[_text("4")]),
        ])
        agent = AnthropicSimpleAgent(
            model="m", kb_path=kb_file, client=client,
            autonomy_policy={"default": "auto", "overrides": {}},
        )
        trace = agent.run({"q": "what is 2+2"}, test_case_id="tc-y")
        assert trace.final_output == "4"
        assert trace.escalated is False
        assert any(s.kind == "tool_call" and s.name == "calculator"
                   for s in trace.spans)

    def test_human_required_via_reference_agent_and_harness(self, tmp_path, kb_file):
        # End-to-end through the harness: the reference agent escalates on a
        # human_required tool; a scripted human authorizes it; the re-invoked
        # agent (guidance present) runs the tool and finishes.
        reg = Registry(tmp_path / "hitl_ref.db")

        def new_client():
            return FakeAnthropicClient([
                NS(stop_reason="tool_use", usage=_usage(),
                   content=[_tool_use("calculator", {"expression": "2+2"})]),
                NS(stop_reason="end_turn", usage=_usage(),
                   content=[_text("4")]),
            ])

        # A fresh client per invocation (the harness calls run() twice).
        clients = [new_client(), new_client()]

        class _RotatingAgent(AnthropicSimpleAgent):
            def run(self, test_input, *, test_case_id=None):
                self.client = clients.pop(0)
                return super().run(test_input, test_case_id=test_case_id)

        agent = _RotatingAgent(
            model="m", kb_path=kb_file, client=clients[0], agent_id="ref-hitl",
            autonomy_policy={"default": "auto",
                             "overrides": {"calculator": "human_required"}},
        )
        cases = [make_case(tags=["should_escalate"])]
        human = ScriptedHuman("Approved.")
        traces = asyncio.run(run_suite(
            agent, make_suite(cases), cases, reg, human=human))

        trace = traces[0]
        assert trace.escalated is True
        assert trace.final_output == "4"
        assert any(s.kind == "escalation" for s in trace.spans)
        # scored appropriately (tagged should_escalate & it escalated)
        assert run_check("escalated_appropriately", trace, cases[0]) == 1.0
        fb = reg.feedback_for_trace(trace.trace_id)
        assert len(fb) == 1 and fb[0].source == "escalation"
