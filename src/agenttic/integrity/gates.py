"""The three integrity gates (SPEC-6 Step 25).

Gates score a case against its *code* criteria only — deterministic, cheap, no
LLM, judge criteria exempt (25.1). "Passes the case" means the weighted mean of
its code-criterion scores clears the pass threshold, exactly as production
scores a run. A case with no code criteria is *inconclusive* (nothing
mechanical to decide), never silently treated as passing.

  * oracle  — the case's own reference solution must pass its checks (25.1).
  * dummy   — the NullAgent must FAIL every case (25.2).
  * exploit — the ExploitAgent must fail every case; any pass is a hole (25.3).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Callable

from agenttic.integrity.agents import ExploitAgent, NullAgent
from agenttic.schema.integrity import GateResult, IntegrityReport
from agenttic.schema.rubric import Rubric
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.scoring.checks import run_check
from agenttic.scoring.engine import DEFAULT_PASS_THRESHOLD

#: cap on how many cases the (paid) exploit gate exercises per suite
EXPLOIT_CASE_CAP = 100


# --------------------------------------------------------------------------- #
# scoring a trace against a case's CODE criteria only
# --------------------------------------------------------------------------- #
def _code_pass(trace: Trace, tc: TestCase, rubric: Rubric,
               threshold: float = DEFAULT_PASS_THRESHOLD) -> tuple[bool | None, dict]:
    """(passed, per-criterion scores) over the case's code criteria. Returns
    (None, {}) when the case has no code criteria (mechanically inconclusive).
    A check that errors on this trace counts as 0.0 (a fail), never a raise."""
    code = [c for c in rubric.criteria if c.scorer == "code" and c.check_ref]
    if not code:
        return None, {}
    scores: dict[str, float] = {}
    for c in code:
        try:
            scores[c.criterion_id] = float(run_check(c.check_ref, trace, tc))
        except Exception:  # noqa: BLE001
            scores[c.criterion_id] = 0.0
    weights = {c.criterion_id: rubric.weights.get(c.criterion_id, 1.0) for c in code}
    total = sum(weights.values()) or 1.0
    weighted = sum(scores[k] * weights[k] for k in scores) / total
    return (weighted >= threshold), scores


def _oracle_trace(tc: TestCase) -> Trace:
    """A synthetic trace standing in for the oracle's behaviour: its final
    output plus a tool_call span per referenced tool (in order)."""
    now = datetime.now(timezone.utc)
    oracle = tc.oracle
    spans = [
        Span(span_id=f"o{i}", kind="tool_call", name=tool, start_time=now, end_time=now)
        for i, tool in enumerate(oracle.tool_sequence or [])
    ] if oracle else []
    return Trace(
        trace_id=f"oracle-{tc.test_id}", agent_id="integrity-oracle",
        agent_config_hash="oracle", test_case_id=tc.test_id, spans=spans,
        visibility="black_box", final_output=oracle.final_output if oracle else "",
        total_steps=len(spans), schema_version=SCHEMA_VERSION)


def _case_payload(tc: TestCase) -> dict:
    """What an adversary sees: the task description + the case input."""
    return {"task_description": tc.task_description, **tc.input}


# --------------------------------------------------------------------------- #
# gates
# --------------------------------------------------------------------------- #
def oracle_gate(cases: list[TestCase], rubric_of: Callable[[str], Rubric]) -> GateResult:
    failing, inconclusive = [], []
    missing = 0
    for tc in cases:
        if tc.oracle is None:
            failing.append(tc.test_id)
            missing += 1
            continue
        ok, _ = _code_pass(_oracle_trace(tc), tc, rubric_of(tc.rubric_id))
        if ok is None:
            inconclusive.append(tc.test_id)   # judge-only: no code checks to prove
        elif not ok:
            failing.append(tc.test_id)          # UNSOLVABLE-AS-WRITTEN
    passed = not failing
    detail = ("every oracle passes its own code checks" if passed else
              f"{len(failing)} case(s) block approval "
              f"({missing} missing an oracle, {len(failing) - missing} UNSOLVABLE-AS-WRITTEN)")
    return GateResult(gate="oracle", ran=True, passed=passed, failing_case_ids=failing,
                      inconclusive_case_ids=inconclusive, detail=detail)


def dummy_gate(cases: list[TestCase], rubric_of: Callable[[str], Rubric]) -> GateResult:
    null = NullAgent()
    failing, inconclusive = [], []
    for tc in cases:
        trace = null.run(_case_payload(tc), test_case_id=tc.test_id)
        ok, _ = _code_pass(trace, tc, rubric_of(tc.rubric_id))
        if ok is None:
            inconclusive.append(tc.test_id)
        elif ok:
            failing.append(tc.test_id)          # the null agent PASSED -> vacuous
    passed = not failing
    detail = ("every case fails the null agent" if passed else
              f"{len(failing)} vacuous case(s) a do-nothing agent passes")
    return GateResult(gate="dummy", ran=True, passed=passed, failing_case_ids=failing,
                      inconclusive_case_ids=inconclusive, detail=detail)


def exploit_gate(cases: list[TestCase], rubric_of: Callable[[str], Rubric],
                 complete: Callable[[str], str], *, save_trace=None,
                 cap: int = EXPLOIT_CASE_CAP) -> GateResult:
    agent = ExploitAgent(complete)
    failing, inconclusive = [], []
    for tc in cases[:cap]:
        trace = agent.run(_case_payload(tc), test_case_id=tc.test_id)
        if save_trace is not None:
            save_trace(trace)                    # mode="integrity" (Hard Rule 29)
        ok, _ = _code_pass(trace, tc, rubric_of(tc.rubric_id))
        if ok is None:
            inconclusive.append(tc.test_id)
        elif ok:
            failing.append(tc.test_id)          # the cheater PASSED -> a hole
    passed = not failing
    detail = ("the exploit agent passed no case" if passed else
              f"{len(failing)} case(s) an explicit cheater passes")
    return GateResult(gate="exploit", ran=True, passed=passed, failing_case_ids=failing,
                      inconclusive_case_ids=inconclusive, detail=detail)


# --------------------------------------------------------------------------- #
# heuristic (no-LLM) cheater — the default adversary when no model is configured
# --------------------------------------------------------------------------- #
_CUES = ("answer is", "should return", "should output", "expected output is",
         "the output is", "output:", "returns", "reply with")


def _leaked_answer(task: str) -> str:
    quoted = re.findall(r'"([^"]{1,200})"', task) or re.findall(r"'([^']{1,200})'", task)
    if quoted:
        return max(quoted, key=len)
    low = task.lower()
    for cue in _CUES:
        idx = low.find(cue)
        if idx >= 0:
            tail = task[idx + len(cue):].strip(" :\t").split("\n")[0].strip(". ")
            if tail:
                return tail[:200]
    return ""


def _mentioned_tools(task: str) -> list[str]:
    return re.findall(r"(?:call|use|invoke)\s+(?:the\s+)?`?([a-z_][a-z0-9_]{2,})`?",
                      task.lower())[:3]


def heuristic_complete(prompt: str) -> str:
    """A deterministic, no-LLM cheater: guess a leaked expected answer from the
    task phrasing and any tool names it mentions. Catches the classic hole —
    the expected output written into the task description — offline and in CI."""
    m = re.search(r"TASK:\s*(.*?)\s*INPUT:", prompt, re.DOTALL)
    task = m.group(1) if m else prompt
    return json.dumps({"final_output": _leaked_answer(task),
                       "tool_sequence": _mentioned_tools(task)})


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def run_integrity_gates(reg, suite: TestSuite, cases: list[TestCase],
                        complete: Callable[[str], str] | None = None) -> IntegrityReport:
    """Run all three gates. `complete` is the exploit model seam; when None a
    deterministic heuristic cheater is used so the gate always runs."""
    cache: dict[str, Rubric] = {}

    def rubric_of(rid: str) -> Rubric:
        if rid not in cache:
            cache[rid] = reg.get_rubric(rid)
        return cache[rid]

    exploit_complete = complete or heuristic_complete
    gates = [
        oracle_gate(cases, rubric_of),
        dummy_gate(cases, rubric_of),
        exploit_gate(cases, rubric_of, exploit_complete,
                     save_trace=lambda t: reg.save_trace(t, mode="integrity")),
    ]
    return IntegrityReport(suite_id=suite.suite_id, version=suite.version, gates=gates)


def verify_suite(reg, cfg, suite_id: str, version: int | None = None,
                 complete: Callable[[str], str] | None = None) -> IntegrityReport:
    """Run the gates over a stored suite and persist the report. Used by
    `ascore verify-suite` and by the generator after drafting a suite."""
    suite, cases = reg.get_suite(suite_id, version)
    report = run_integrity_gates(reg, suite, cases, complete=complete)
    reg.save_integrity_report(report)
    # SPEC-6 26.1: the ABC benchmark-rigor scorecard, now that gate evidence exists.
    try:
        from agenttic.integrity.abc import compute_abc_report
        reg.save_abc_report(compute_abc_report(reg, suite_id, suite.version))
    except Exception:  # noqa: BLE001 — the rigor report must never block verify
        pass
    return report
