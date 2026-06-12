"""Step 2 acceptance tests (SPEC.md):
- Adapter returns a valid Trace with >=3 spans on a sample input
- Cost and token counts populated from API usage data
- A forced tool error produces an error span, not a crash

Uses a scripted fake client (Hard Rule 8: pipeline tests run with mocked
LLM calls). A real-key smoke test is a one-liner on a dev machine.
"""

import json
from types import SimpleNamespace as NS

import pytest

from ascore.adapters.anthropic_simple import AnthropicSimpleAgent, _safe_eval
from ascore.schema.trace import Trace


def usage(i=120, o=45):
    return NS(input_tokens=i, output_tokens=o)


def tool_use(name, args, id_="tu_1"):
    return NS(type="tool_use", name=name, input=args, id=id_)


def text(t):
    return NS(type="text", text=t)


class FakeClient:
    """Replays a scripted sequence of API responses and records requests."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.messages = NS(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeClient ran out of scripted responses")
        return self._responses.pop(0)


@pytest.fixture
def kb_file(tmp_path):
    p = tmp_path / "kb.json"
    p.write_text(json.dumps({"refund_policy": "30 days, full refund"}))
    return p


def make_agent(responses, kb_file, **kw):
    return AnthropicSimpleAgent(
        model="claude-test-model",
        kb_path=kb_file,
        client=FakeClient(responses),
        **kw,
    )


HAPPY_SCRIPT = [
    NS(stop_reason="tool_use", usage=usage(),
       content=[tool_use("calculator", {"expression": "17 * 4"})]),
    NS(stop_reason="tool_use", usage=usage(),
       content=[tool_use("lookup_kb", {"key": "refund_policy"}, id_="tu_2")]),
    NS(stop_reason="end_turn", usage=usage(140, 60),
       content=[text("68. Refunds: 30 days, full refund.")]),
]


class TestHappyPath:
    def test_valid_trace_with_min_spans(self, kb_file):
        trace = make_agent(HAPPY_SCRIPT, kb_file).run(
            {"question": "what is 17*4 and the refund policy?"}, test_case_id="tc-1"
        )
        assert isinstance(trace, Trace)
        # 3 llm calls + 2 tool calls + final_output = 6 spans
        assert len(trace.spans) >= 3
        kinds = [s.kind for s in trace.spans]
        assert kinds.count("llm_call") == 3
        assert kinds.count("tool_call") == 2
        assert kinds[-1] == "final_output"
        assert trace.final_output.startswith("68")
        assert trace.test_case_id == "tc-1"
        assert trace.visibility == "glass_box"
        assert trace.total_steps == 5

    def test_tokens_and_cost_populated_from_usage(self, kb_file):
        trace = make_agent(HAPPY_SCRIPT, kb_file).run({"q": "x"})
        llm_spans = [s for s in trace.spans if s.kind == "llm_call"]
        assert all(s.tokens_in and s.tokens_out for s in llm_spans)
        assert all(s.cost_usd and s.cost_usd > 0 for s in llm_spans)
        expected_total = sum(s.cost_usd for s in llm_spans)
        assert trace.total_cost_usd == pytest.approx(expected_total)

    def test_tool_results_fed_back_to_model(self, kb_file):
        agent = make_agent(HAPPY_SCRIPT, kb_file)
        agent.run({"q": "x"})
        second_request = agent.client.requests[1]
        tool_result_turn = second_request["messages"][-1]
        assert tool_result_turn["content"][0]["content"] == "68"


class TestErrorHandling:
    def test_forced_tool_error_yields_error_span_not_crash(self, kb_file):
        script = [
            NS(stop_reason="tool_use", usage=usage(),
               content=[tool_use("calculator", {"expression": "1/0"})]),
            NS(stop_reason="end_turn", usage=usage(),
               content=[text("That expression is undefined.")]),
        ]
        trace = make_agent(script, kb_file).run({"q": "divide by zero"})
        err = [s for s in trace.spans if s.kind == "tool_call" and s.error]
        assert len(err) == 1
        assert "ZeroDivisionError" in err[0].error
        assert trace.final_output  # run completed normally

    def test_unknown_kb_key_is_error_data(self, kb_file):
        script = [
            NS(stop_reason="tool_use", usage=usage(),
               content=[tool_use("lookup_kb", {"key": "ghost"})]),
            NS(stop_reason="end_turn", usage=usage(), content=[text("Not found.")]),
        ]
        trace = make_agent(script, kb_file).run({"q": "x"})
        errs = [s.error for s in trace.spans if s.error]
        assert any("key not found" in e for e in errs)

    def test_max_steps_kill_switch(self, kb_file):
        loop = NS(stop_reason="tool_use", usage=usage(),
                  content=[tool_use("calculator", {"expression": "1+1"})])
        trace = make_agent([loop] * 3, kb_file, max_steps=3).run({"q": "loop"})
        assert trace.final_output == "MAX_STEPS_EXCEEDED"
        assert any(s.kind == "error" and "max_steps" in s.name for s in trace.spans)


class TestConfigHash:
    def test_hash_stable_and_sensitive(self, kb_file):
        a = make_agent([], kb_file)
        b = make_agent([], kb_file)
        c = make_agent([], kb_file, max_steps=99)
        assert a.config_hash() == b.config_hash()
        assert a.config_hash() != c.config_hash()


class TestSafeEval:
    @pytest.mark.parametrize("expr,val", [("2+3*4", 14), ("(1+1)**3", 8), ("-5+2", -3)])
    def test_arithmetic(self, expr, val):
        assert _safe_eval(expr) == val

    def test_code_injection_blocked(self):
        with pytest.raises(Exception):
            _safe_eval("__import__('os').system('rm -rf /')")
