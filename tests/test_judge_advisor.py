"""Tiered judge via the advisor tool (beta advisor-tool-2026-03-01).

Covers: request shape (beta header, advisor tool definition), parsing when
the executor emits preamble text before the advisor call, pause_turn
resumption, Hard Rule 4 on the advisor model, and make_judge selection.
"""

import json
from types import SimpleNamespace as NS

import pytest

from ascore.scoring.judge import ADVISOR_BETA, JudgeError, LLMJudge, make_judge
from tests.test_judge_calibration import TONE, make_tc, make_trace

VERDICT = json.dumps({"score": 1, "rationale": "meets the pass anchor"})


def text(t):
    return NS(type="text", text=t)


def advisor_blocks():
    return [
        NS(type="server_tool_use", id="srv1", name="advisor", input={}),
        NS(type="advisor_tool_result", tool_use_id="srv1",
           content=NS(type="advisor_result", text="Lean toward pass.")),
    ]


class FakeBetaClient:
    """Captures beta.messages.create kwargs; replies are (stop_reason, content)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))
        self.messages = NS(create=self._fail)  # advisor judge must not use GA path

    def _create(self, **kw):
        self.requests.append(kw)
        stop_reason, content = self.replies.pop(0)
        return NS(stop_reason=stop_reason, content=content)

    @staticmethod
    def _fail(**kw):
        raise AssertionError("advisor judge called client.messages.create")


def make_advisor_judge(replies, **kw):
    client = FakeBetaClient(replies)
    defaults = dict(model="claude-sonnet-4-6", agent_model="some-agent",
                    advisor_model="claude-opus-4-8")
    defaults.update(kw)
    return LLMJudge(client=client, **defaults), client


CFG = {
    "models": {
        "agent_default": "claude-sonnet-4-6",
        "judge_executor": "claude-sonnet-4-6",
        "judge_strong": "claude-opus-4-8",
    },
    "scoring": {"advisor_max_tokens": 2048},
}


class TestAdvisorRequestShape:
    def test_beta_header_and_tool_definition(self):
        judge, client = make_advisor_judge([("end_turn", [text(VERDICT)])])
        judge.score_criterion(TONE, make_trace(), make_tc())
        req = client.requests[0]
        assert req["betas"] == [ADVISOR_BETA]
        (tool,) = req["tools"]
        assert tool["type"] == "advisor_20260301"
        assert tool["name"] == "advisor"
        assert tool["model"] == "claude-opus-4-8"
        assert tool["max_tokens"] == 2048
        assert tool["max_uses"] == 1

    def test_preamble_then_advisor_then_verdict_parses_last_text(self):
        content = [text("Borderline — consulting the advisor."),
                   *advisor_blocks(), text(VERDICT)]
        judge, _ = make_advisor_judge([("end_turn", content)])
        cs = judge.score_criterion(TONE, make_trace(), make_tc())
        assert cs.score == 1.0
        assert cs.judge_rationale == "meets the pass anchor"

    def test_pause_turn_resumes_with_assistant_content(self):
        dangling = [text("Consulting."),
                    NS(type="server_tool_use", id="srv1", name="advisor", input={})]
        judge, client = make_advisor_judge([
            ("pause_turn", dangling),
            ("end_turn", [*advisor_blocks(), text(VERDICT)]),
        ])
        cs = judge.score_criterion(TONE, make_trace(), make_tc())
        assert cs.score == 1.0
        assert len(client.requests) == 2
        resumed = client.requests[1]["messages"]
        assert resumed[1] == {"role": "assistant", "content": dangling}

    def test_endless_pause_turn_raises(self):
        dangling = ("pause_turn", [text("...")])
        judge, _ = make_advisor_judge([dangling] * 10)
        with pytest.raises(JudgeError, match="pause_turn"):
            judge.score_criterion(TONE, make_trace(), make_tc())


class TestHardRule4:
    def test_advisor_model_must_differ_from_agent(self):
        with pytest.raises(ValueError, match="Hard Rule 4"):
            LLMJudge(model="executor", agent_model="claude-opus-4-8",
                     advisor_model="claude-opus-4-8", client=object())


class TestMakeJudge:
    def test_distinct_agent_gets_tiered_judge(self):
        judge = make_judge(CFG, "claude-haiku-4-5-20251001", client=object())
        assert judge.model == "claude-sonnet-4-6"
        assert judge.advisor_model == "claude-opus-4-8"
        assert judge.advisor_max_tokens == 2048

    def test_blackbox_agent_gets_tiered_judge(self):
        judge = make_judge(CFG, "blackbox:client-x", client=object())
        assert judge.advisor_model == "claude-opus-4-8"

    def test_sonnet_agent_falls_back_to_plain_strong_judge(self):
        judge = make_judge(CFG, "claude-sonnet-4-6", client=object())
        assert judge.model == "claude-opus-4-8"
        assert judge.advisor_model is None

    def test_no_executor_configured_falls_back(self):
        cfg = {"models": {"judge_strong": "claude-opus-4-8"}}
        judge = make_judge(cfg, "claude-sonnet-4-6", client=object())
        assert judge.model == "claude-opus-4-8"
        assert judge.advisor_model is None
