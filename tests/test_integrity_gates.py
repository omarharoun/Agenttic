"""SPEC-6 Step 25 — the three integrity gates + the approve/waive flow."""

from __future__ import annotations

import pytest

from agenttic.integrity import verify_suite
from agenttic.registry.sqlite_store import IntegrityError, Registry
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import OracleSolution, TestCase, TestSuite


def _rubric(rid: str, cid: str, check: str) -> Rubric:
    return Rubric(rubric_id=rid, version=1, weights={cid: 1.0}, criteria=[
        Criterion(criterion_id=cid, description=cid, scorer="code",
                  scale="binary", check_ref=check)])


@pytest.fixture
def reg(tmp_path):
    r = Registry(tmp_path / "t.db")
    r.save_rubric(_rubric("rb_match", "match", "final_output_matches_expected"))
    r.save_rubric(_rubric("rb_steps", "steps", "steps_under_limit"))

    cases = [
        # clean: oracle passes, null fails, no leaked answer
        TestCase(test_id="c_ok", suite_id="s", task_description="Compute the sum.",
                 expected={"final_output": "the sum is 42"}, rubric_id="rb_match",
                 oracle=OracleSolution(final_output="the sum is 42")),
        # broken oracle: reference output does NOT pass the case's own check
        TestCase(test_id="c_broken", suite_id="s", task_description="Return the code.",
                 expected={"final_output": "result: 7"}, rubric_id="rb_match",
                 oracle=OracleSolution(final_output="totally wrong")),
        # guessable: the expected answer is leaked, in quotes, in the task text
        TestCase(test_id="c_leak", suite_id="s",
                 task_description='Name the animal. The answer is "platypus".',
                 expected={"final_output": "platypus"}, rubric_id="rb_match",
                 oracle=OracleSolution(final_output="platypus")),
        # vacuous: the only check (steps<=5) is satisfied by a do-nothing agent
        TestCase(test_id="c_vacuous", suite_id="s", task_description="Do the thing.",
                 expected={"max_steps": 5}, rubric_id="rb_steps",
                 oracle=OracleSolution(final_output="done")),
    ]
    r.save_suite(TestSuite(suite_id="s", version=1, business_context="pilot",
                           test_ids=[c.test_id for c in cases]), cases)
    return r


def test_oracle_gate_flags_broken_oracle(reg):
    report = verify_suite(reg, {}, "s")
    oracle = report.get("oracle")
    assert "c_broken" in oracle.failing_case_ids     # UNSOLVABLE-AS-WRITTEN
    assert "c_ok" not in oracle.failing_case_ids
    assert not oracle.passed


def test_dummy_gate_flags_vacuous_case(reg):
    dummy = verify_suite(reg, {}, "s").get("dummy")
    assert "c_vacuous" in dummy.failing_case_ids      # null agent passes it
    assert "c_ok" not in dummy.failing_case_ids
    assert not dummy.passed


def test_exploit_gate_flags_guessable_case_and_stores_trajectory(reg):
    report = verify_suite(reg, {}, "s")
    exploit = report.get("exploit")
    assert "c_leak" in exploit.failing_case_ids       # the cheater guessed it
    assert exploit.ran
    # the cheating trajectory is stored, isolated as mode="integrity"
    integ = reg.traces("integrity-exploit", mode="integrity")
    leak = [t for t in integ if t.test_case_id == "c_leak"]
    assert leak and leak[0].final_output == "platypus"
    # and it never leaks into batch
    assert reg.traces("integrity-exploit", mode="batch") == []


def test_approve_refuses_until_gates_clear_then_waivers_unblock(reg):
    verify_suite(reg, {}, "s")
    # unwaived failing gates -> approval refused (Hard Rule 27)
    with pytest.raises(IntegrityError):
        reg.assert_integrity_clear("s", 1)

    # waiving requires a named gate + reason, recorded on the suite version
    for gate in ("oracle", "dummy", "exploit"):
        reg.waive_gate("s", 1, gate, reason=f"pilot: {gate} reviewed by hand")
    report = reg.get_integrity_report("s", 1)
    assert all(g.waived and g.waiver_reason for g in report.gates)

    # now approval is permitted
    reg.assert_integrity_clear("s", 1)
    reg.approve_suite("s", 1)
    suite, _ = reg.get_suite("s", 1)
    assert suite.approved


def test_waive_unknown_gate_raises(reg):
    verify_suite(reg, {}, "s")
    with pytest.raises(Exception):
        reg.waive_gate("s", 1, "not_a_gate", "x")
