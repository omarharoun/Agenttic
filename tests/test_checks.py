"""Step 4 acceptance tests (SPEC.md):
- Each check unit-tested against hand-built traces (pass + fail fixtures)
- Unknown check_ref in a rubric fails loudly at suite-load time
"""

import uuid
from datetime import datetime, timezone

import pytest

from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.scoring.checks import (
    CHECKS,
    CheckConfigError,
    UnknownCheckError,
    check,
    run_check,
    validate_rubric_checks,
)

NOW = datetime(2026, 6, 11, tzinfo=timezone.utc)


def span(kind, name):
    return Span(span_id=uuid.uuid4().hex[:12], kind=kind, name=name,
                start_time=NOW, end_time=NOW)


def trace(final_output="ok", tools=(), steps=2, cost=0.01):
    spans = [span("llm_call", "model")] + [span("tool_call", t) for t in tools]
    spans.append(span("final_output", "final_output"))
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id="tc-1", spans=spans, visibility="glass_box",
                 final_output=final_output, total_cost_usd=cost,
                 total_steps=steps, schema_version=SCHEMA_VERSION)


def tc(expected):
    return TestCase(test_id="tc-1", suite_id="s-1", task_description="t",
                    input={}, expected=expected, rubric_id="r-1")


class TestEachCheckPassAndFail:
    CASES = [
        ("final_output_matches_expected",
         trace(final_output="  billing  "), trace(final_output="sales"),
         {"final_output": "billing"}),
        ("valid_json_output",
         trace(final_output='{"queue": "billing"}'), trace(final_output="not json {"),
         {}),
        ("required_tool_called",
         trace(tools=("lookup_kb", "calculator")), trace(tools=("calculator",)),
         {"required_tools": ["lookup_kb"]}),
        ("forbidden_tool_not_called",
         trace(tools=("lookup_kb",)), trace(tools=("issue_refund", "lookup_kb")),
         {"forbidden_tools": ["issue_refund"]}),
        ("steps_under_limit",
         trace(steps=3), trace(steps=9),
         {"max_steps": 5}),
        ("cost_under_limit",
         trace(cost=0.01), trace(cost=0.50),
         {"max_cost_usd": 0.05}),
    ]

    @pytest.mark.parametrize("name,passing,failing,expected", CASES,
                             ids=[c[0] for c in CASES])
    def test_pass_and_fail_fixtures(self, name, passing, failing, expected):
        assert run_check(name, passing, tc(expected)) == 1.0
        assert run_check(name, failing, tc(expected)) == 0.0


class TestConfigErrors:
    def test_missing_expected_key_is_authoring_bug_not_agent_fail(self):
        with pytest.raises(CheckConfigError, match="required_tools"):
            run_check("required_tool_called", trace(), tc({}))

    def test_expected_none_raises(self):
        with pytest.raises(CheckConfigError):
            run_check("steps_under_limit", trace(), tc(None))


class TestRegistry:
    def test_all_six_mvp_checks_registered(self):
        assert {
            "final_output_matches_expected", "valid_json_output",
            "required_tool_called", "forbidden_tool_not_called",
            "steps_under_limit", "cost_under_limit",
        } <= set(CHECKS)

    def test_duplicate_registration_rejected(self):
        with pytest.raises(ValueError, match="already registered"):
            @check("valid_json_output")
            def dupe(t, c):  # pragma: no cover
                return 1.0

    def test_unknown_check_at_run_time_raises(self):
        with pytest.raises(UnknownCheckError):
            run_check("ghost_check", trace(), tc({}))


class TestLoadTimeValidation:
    def test_unknown_check_ref_fails_at_load_not_scoring(self):
        rubric = Rubric(rubric_id="r-1", criteria=[
            Criterion(criterion_id="c1", description="d", scorer="code",
                      scale="binary", check_ref="does_not_exist"),
        ])
        with pytest.raises(UnknownCheckError, match="does_not_exist"):
            validate_rubric_checks(rubric)

    def test_valid_rubric_passes_load(self):
        rubric = Rubric(rubric_id="r-1", criteria=[
            Criterion(criterion_id="c1", description="d", scorer="code",
                      scale="binary", check_ref="valid_json_output"),
            Criterion(criterion_id="c2", description="d", scorer="judge",
                      scale="binary",
                      anchors={"pass": "p", "fail": "f"}),  # judge crit ignored here
        ])
        validate_rubric_checks(rubric)  # no raise
