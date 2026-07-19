"""SPEC-7 Step 30 — the user simulator + conversation loop + labeling."""

from __future__ import annotations

import uuid

from agenttic.adapters.base import AgentAdapter, EscalationRequired
from agenttic.harness.user_sim import run_conversation
from agenttic.reporting.scorecard_report import render_markdown
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.schema.user_scenario import UserScenario
from agenttic.scoring.checks import run_check


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _trace(agent_id, text):
    now = _now()
    return Trace(trace_id=uuid.uuid4().hex, agent_id=agent_id, agent_config_hash="h",
                 spans=[Span(span_id="f", kind="final_output", name="final_output",
                             start_time=now, end_time=now, output={"text": text})],
                 visibility="glass_box", final_output=text, schema_version=SCHEMA_VERSION)


class AskingAgent(AgentAdapter):
    """Asks for the account number (eliciting the hidden fact), then answers."""
    visibility = "glass_box"
    agent_id = "asker"

    def describe(self): return {"adapter": "AskingAgent"}

    def run(self, test_input, *, test_case_id=None):
        convo = test_input.get("conversation", [])
        last_user = convo[-1]["content"] if convo else ""
        if "account" in last_user.lower():   # the user revealed it after we asked
            return _trace(self.agent_id, f"Thanks — refund issued to {last_user}. DONE")
        return _trace(self.agent_id, "What is your account number?")


class LazyAgent(AgentAdapter):
    """Never asks; just guesses — so the hidden fact is never revealed."""
    visibility = "glass_box"
    agent_id = "lazy"

    def describe(self): return {"adapter": "LazyAgent"}

    def run(self, test_input, *, test_case_id=None):
        return _trace(self.agent_id, "Refund issued. DONE")


def _case():
    return TestCase(test_id="c1", suite_id="s", task_description="I want a refund.",
                    input={}, rubric_id="rb",
                    user_scenario=UserScenario(
                        goal="I want a refund on my order.",
                        hidden_facts={"account": {"value": "AC-42",
                                                  "reveal_when": "account number"}},
                        max_turns=6))


def test_elicit_hidden_fact_passes_only_when_agent_asks():
    # the asking agent elicits the account number; the transcript contains it
    convo = run_conversation(AskingAgent(), _case())
    assert convo.user_source == "simulated"
    assert "AC-42" in convo.final_output

    # the lazy agent never asks -> the fact is never revealed
    lazy = run_conversation(LazyAgent(), _case())
    assert "AC-42" not in lazy.final_output


def test_required_info_conveyed_check():
    tc = TestCase(test_id="c1", suite_id="s", task_description="t", rubric_id="rb",
                  expected={"must_convey": ["5-7 business days"]})
    good = _trace("a", "Your refund takes 5-7 business days.")
    assert run_check("required_info_conveyed", good, tc) == 1.0
    bad = _trace("a", "Your refund is processed.")
    assert run_check("required_info_conveyed", bad, tc) == 0.0


class EscalatingAgent(AgentAdapter):
    """Escalates on the first turn, then completes once guided."""
    visibility = "glass_box"
    agent_id = "escalator"

    def describe(self): return {"adapter": "EscalatingAgent"}

    def run(self, test_input, *, test_case_id=None):
        if "human_guidance" not in test_input:
            raise EscalationRequired("Authorize refund?", context={"tool": "issue_refund"},
                                     partial_trace_spans=[])
        return _trace(self.agent_id, "Refund authorized and issued. DONE")


class _Human:
    def respond(self, question, context): return "approved"


def test_escalation_mid_conversation_records_and_resumes():
    convo = run_conversation(EscalatingAgent(), _case(), human=_Human())
    assert convo.escalated is True
    assert "authorized" in convo.final_output.lower()


class StallingAgent(AgentAdapter):
    """Never finishes — forces max-turns exhaustion."""
    visibility = "glass_box"
    agent_id = "staller"

    def describe(self): return {"adapter": "StallingAgent"}

    def run(self, test_input, *, test_case_id=None):
        return _trace(self.agent_id, "Could you tell me more?")


def test_max_turns_exhaustion_yields_persisted_failure_trace():
    convo = run_conversation(StallingAgent(), _case())
    assert convo.final_output == "MAX_TURNS_EXHAUSTED"
    assert any(s.kind == "error" and s.name == "max_turns_exhausted" for s in convo.spans)
    assert convo.user_source == "simulated"     # still labelled, still persisted


def test_scorecard_and_report_carry_simulated_label():
    runs = [RunScore(trace_id="t", test_id="c1", passed=True, user_source="simulated",
                     criterion_scores=[CriterionScore(criterion_id="m", score=1.0, scorer="code")])]
    sc = Scorecard.aggregate(scorecard_id="sc1", agent_id="a", suite_id="s",
                             suite_version=1, rubric_id="rb", rubric_version=1,
                             run_scores=runs, visibility_tier="glass_box")
    assert sc.user_source == "simulated"
    rubric = Rubric(rubric_id="rb", version=1, weights={"m": 1.0}, criteria=[
        Criterion(criterion_id="m", description="d", scorer="code", scale="binary",
                  check_ref="final_output_matches_expected")])
    assert "simulated users" in render_markdown(sc, rubric)
