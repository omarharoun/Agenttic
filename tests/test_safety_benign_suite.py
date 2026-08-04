"""Over-refusal has to be measurable, or refusing everything is a winning move.

`std-safety-refusal-v1` is 4/4 harmful and `agentharm-harmful-v1` is 6/6. With
no benign case anywhere, an agent that refuses every request scored a PERFECT
safety result, and the fault-injection gate had to exempt it — because on those
suites refusing genuinely IS correct. That exemption was the last blanket one in
the gate, and it existed because the suites could not tell caution from
uselessness.

`std-safety-benign-v1` is the other half: the legitimate twin of each harmful
case, same tool families, honest intent. Refusing one of these is the failure.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from agenttic.metrics.standard_suites import standard_specs
from agenttic.schema.trace import Span, Trace
from agenttic.scoring.engine import score_run

NOW = datetime(2026, 8, 2, tzinfo=timezone.utc)
DID_IT = "Done — here is the result you asked for."
REFUSED = "I cannot help with that request."


@pytest.fixture(scope="module")
def spec():
    return next(s for s in standard_specs()
                if s.suite.suite_id == "std-safety-benign-v1")


def run(case, rubric, text, tools=()):
    spans = [Span(span_id=f"t{i}", kind="tool_call", name=n, start_time=NOW,
                  end_time=NOW, input={}) for i, n in enumerate(tools)]
    spans.append(Span(span_id="f", kind="llm_call", name="answer",
                      start_time=NOW, end_time=NOW, output={"text": text}))
    return score_run(Trace(trace_id=uuid.uuid4().hex, agent_id="a",
                           agent_config_hash="h", test_case_id=case.test_id,
                           visibility="glass_box", final_output=text,
                           spans=spans), case, rubric)


class TestItMeasuresOverRefusal:
    def test_an_agent_that_refuses_everything_fails_every_case(self, spec):
        """The whole point. On the harmful suites this agent scores perfectly."""
        n = sum(bool(run(c, spec.rubric, REFUSED).passed) for c in spec.cases)
        assert n == 0

    def test_an_agent_that_does_the_work_passes_every_case(self, spec):
        n = sum(bool(run(c, spec.rubric, DID_IT,
                         (c.expected["required_tools"][0],)).passed)
                for c in spec.cases)
        assert n == len(spec.cases)

    def test_talking_without_acting_is_not_completion(self, spec):
        """Saying "done" without calling the tool is the silent no-op."""
        n = sum(bool(run(c, spec.rubric, DID_IT).passed) for c in spec.cases)
        assert n == 0


class TestTheSuiteItself:
    def test_every_case_is_benign_and_names_its_legitimate_tool(self, spec):
        assert spec.cases
        for c in spec.cases:
            assert c.expected.get("harmful") is False
            assert c.expected.get("required_tools"), c.test_id

    def test_no_case_is_tagged_adversarial(self, spec):
        """An adversarial tag would make `case_warrants_refusal` true and pull
        refusal criteria in, inverting what this suite measures."""
        for c in spec.cases:
            assert "adversarial" not in (c.tags or [])

    def test_it_pairs_with_the_harmful_suite_one_for_one(self, spec):
        harmful = next(s for s in standard_specs()
                       if s.suite.suite_id == "std-safety-refusal-v1")
        assert len(spec.cases) == len(harmful.cases), (
            "the benign twin set should match the harmful set case for case, so "
            "the pair measures discrimination rather than caution")

    def test_it_carries_no_refusal_criterion(self, spec):
        ids = {c.criterion_id for c in spec.rubric.criteria}
        assert "harmful_action_refused" not in ids

    def test_it_is_a_separate_suite_on_purpose(self):
        """Benign cases inside the refusal suite would have NO applicable
        criteria — `applicable_to_case` drops refusal criteria from a
        non-adversarial case — and an empty criteria list scores an unfailable
        pass (engine.py:172-181)."""
        from agenttic.scoring.engine import applicable_to_case

        harmful = next(s for s in standard_specs()
                       if s.suite.suite_id == "std-safety-refusal-v1")
        benign = next(s for s in standard_specs()
                      if s.suite.suite_id == "std-safety-benign-v1")
        left = applicable_to_case(list(harmful.rubric.criteria), benign.cases[0])
        assert not left, (
            "a benign case under the refusal rubric has no applicable criteria "
            "— which is exactly why this is a separate suite")


class TestTheGateNoLongerHasToExemptIt:
    def test_no_degenerate_passes_the_benign_suite(self):
        from agenttic.verification.selfcheck import (DEGENERATE_PROBES,
                                                     run_probe, shipped_suites)

        sid, cases, rubric = next(
            (s for s in shipped_suites() if s[0] == "std-safety-benign-v1"))
        for pid, probe in DEGENERATE_PROBES.items():
            out = run_probe(probe, cases, rubric, suite_id=sid, probe_id=pid)
            assert out.passed_cases == 0, f"{pid} passed {out.passed_cases}"
