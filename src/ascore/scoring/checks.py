"""Deterministic checks — the code half of the scoreboard.

A check is a pure function ``(trace, test_case) -> float`` returning a score
in {0.0, 1.0}. Checks read configuration from ``test_case.expected``:

    final_output_matches_expected -> expected["final_output"]
    required_tool_called          -> expected["required_tools"]: list[str]
    forbidden_tool_not_called     -> expected["forbidden_tools"]: list[str]
    steps_under_limit             -> expected["max_steps"]: int
    cost_under_limit              -> expected["max_cost_usd"]: float
    valid_json_output             -> (no config)

Misconfigured checks (missing expected keys) raise ``CheckConfigError`` —
that is a test-authoring bug, distinct from an agent failure (score 0.0).
Unknown ``check_ref`` values in a rubric fail loudly at suite-LOAD time via
``validate_rubric_checks`` (SPEC.md Step 4 acceptance criterion).
"""

from __future__ import annotations

import json
from typing import Callable

from ascore.schema.rubric import Rubric
from ascore.schema.testcase import TestCase
from ascore.schema.trace import Trace

CheckFn = Callable[[Trace, TestCase], float]

CHECKS: dict[str, CheckFn] = {}


class CheckConfigError(ValueError):
    """The test case lacks the configuration a check needs."""


class UnknownCheckError(KeyError):
    """A rubric references a check_ref that is not registered."""


def check(name: str) -> Callable[[CheckFn], CheckFn]:
    """Register a deterministic check under ``name``."""
    def deco(fn: CheckFn) -> CheckFn:
        if name in CHECKS:
            raise ValueError(f"check {name!r} already registered")
        CHECKS[name] = fn
        return fn
    return deco


def run_check(name: str, trace: Trace, tc: TestCase) -> float:
    if name not in CHECKS:
        raise UnknownCheckError(name)
    return CHECKS[name](trace, tc)


def validate_rubric_checks(rubric: Rubric) -> None:
    """Fail loudly at suite-load time if any code criterion references an
    unregistered check (never defer this to scoring time)."""
    missing = [
        (c.criterion_id, c.check_ref)
        for c in rubric.criteria
        if c.scorer == "code" and c.check_ref not in CHECKS
    ]
    if missing:
        raise UnknownCheckError(
            f"rubric {rubric.rubric_id} v{rubric.version} references unknown "
            f"checks: {missing}; registered: {sorted(CHECKS)}"
        )


def _need(tc: TestCase, key: str):
    if tc.expected is None or key not in tc.expected:
        raise CheckConfigError(
            f"test {tc.test_id}: check requires expected[{key!r}]"
        )
    return tc.expected[key]


def _tool_calls(trace: Trace) -> set[str]:
    return {s.name for s in trace.spans if s.kind == "tool_call"}


# -- MVP checks -------------------------------------------------------------

@check("final_output_matches_expected")
def final_output_matches_expected(trace: Trace, tc: TestCase) -> float:
    expected = str(_need(tc, "final_output"))
    return 1.0 if trace.final_output.strip() == expected.strip() else 0.0


@check("valid_json_output")
def valid_json_output(trace: Trace, tc: TestCase) -> float:
    try:
        json.loads(trace.final_output)
        return 1.0
    except (json.JSONDecodeError, TypeError):
        return 0.0


@check("required_tool_called")
def required_tool_called(trace: Trace, tc: TestCase) -> float:
    required = set(_need(tc, "required_tools"))
    return 1.0 if required <= _tool_calls(trace) else 0.0


@check("forbidden_tool_not_called")
def forbidden_tool_not_called(trace: Trace, tc: TestCase) -> float:
    forbidden = set(_need(tc, "forbidden_tools"))
    return 1.0 if not (forbidden & _tool_calls(trace)) else 0.0


@check("steps_under_limit")
def steps_under_limit(trace: Trace, tc: TestCase) -> float:
    return 1.0 if trace.total_steps <= int(_need(tc, "max_steps")) else 0.0


@check("cost_under_limit")
def cost_under_limit(trace: Trace, tc: TestCase) -> float:
    return 1.0 if trace.total_cost_usd <= float(_need(tc, "max_cost_usd")) else 0.0
