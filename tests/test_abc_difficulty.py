"""SPEC-6 Step 26 — ABC benchmark-rigor scorecard + empirical difficulty."""

from __future__ import annotations

import pytest

from agenttic.integrity import verify_suite
from agenttic.integrity.difficulty import case_difficulty
from agenttic.generator.quality import compute_generator_report
from agenttic.ops import report_op
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.testcase import OracleSolution, TestCase, TestSuite


def _rubric():
    return Rubric(rubric_id="rb", version=1, weights={"match": 1.0, "tone": 1.0},
                 criteria=[
                     Criterion(criterion_id="match", description="d", scorer="code",
                               scale="binary", check_ref="final_output_matches_expected"),
                     Criterion(criterion_id="tone", description="courteous", scorer="judge",
                               scale="three_point", anchors={"pass": "warm", "fail": "curt"})])


def _case(tid, ans, tags=None):
    return TestCase(test_id=tid, suite_id="s", task_description="Compute.",
                    expected={"final_output": ans}, rubric_id="rb", tags=tags or [],
                    oracle=OracleSolution(final_output=ans))


def _scorecard(reg, agent_id, passes: dict[str, bool], version=1):
    runs = [RunScore(trace_id=f"{agent_id}-{tid}", test_id=tid,
                     criterion_scores=[CriterionScore(criterion_id="match",
                                       score=1.0 if ok else 0.0, scorer="code")],
                     passed=ok) for tid, ok in passes.items()]
    sc = Scorecard.aggregate(scorecard_id=f"sc-{agent_id}", agent_id=agent_id,
                             suite_id="s", suite_version=version, rubric_id="rb",
                             rubric_version=1, run_scores=runs, visibility_tier="glass_box")
    reg.save_scorecard(sc)
    return sc


@pytest.fixture
def reg(tmp_path):
    r = Registry(tmp_path / "t.db")
    r.save_rubric(_rubric())
    cases = [_case("A", "a"), _case("B", "b"), _case("C", "c")]
    r.save_suite(TestSuite(suite_id="s", version=1, business_context="pilot",
                           test_ids=[c.test_id for c in cases]), cases)
    return r


def test_abc_report_computes_with_honest_na_and_renders(reg):
    verify_suite(reg, {}, "s")                 # stores integrity + ABC report
    abc = reg.get_abc_report("s", 1)
    assert abc is not None and abc.overall is not None
    by_id = {i.item_id: i for i in abc.items}
    # solvability/guessing/exploitation evidenced from the gates
    assert by_id["I.a"].status == "computed"
    # judge criterion present but uncalibrated -> honest 0.0, not omitted
    assert by_id["I.d"].status == "computed" and by_id["I.d"].score == 0.0
    # contamination not evidenceable yet -> N/A, never estimated upward
    assert by_id["III.3"].score is None and by_id["III.3"].status == "n/a"

    # renders into the client scorecard report
    sc = _scorecard(reg, "cfgA", {"A": True, "B": False, "C": True})
    md = report_op(reg, sc.scorecard_id)
    assert "Benchmark rigor" in md and "(ABC)" in md and "| I.a |" in md


def test_difficulty_bands_and_zero_discrimination(reg):
    # three distinct configs; A always passes, B always fails, C is mixed
    _scorecard(reg, "cfg1", {"A": True, "B": False, "C": True})
    _scorecard(reg, "cfg2", {"A": True, "B": False, "C": True})
    _scorecard(reg, "cfg3", {"A": True, "B": False, "C": False})
    diff, note = case_difficulty(reg, "s")
    assert note == ""
    assert diff["A"]["band"] == "easy" and diff["A"]["zero_discrimination"]      # all pass
    assert diff["B"]["band"] == "hard" and diff["B"]["zero_discrimination"]      # all fail
    assert diff["C"]["band"] == "easy" and not diff["C"]["zero_discrimination"]  # 2/3, discriminates


def test_difficulty_needs_three_configs(reg):
    _scorecard(reg, "cfg1", {"A": True})
    _scorecard(reg, "cfg2", {"A": True})
    diff, note = case_difficulty(reg, "s")
    assert diff == {} and ">=3 agent configs" in note


def test_generator_report_predicted_vs_empirical_agreement(tmp_path):
    r = Registry(tmp_path / "g.db")
    r.save_rubric(_rubric())
    # a case the generator predicted "easy" that empirically is easy (all pass)
    cases = [_case("A", "a", tags=["easy"]), _case("B", "b", tags=["hard"])]
    r.save_suite(TestSuite(suite_id="s", version=1, business_context="c",
                           test_ids=["A", "B"]), cases)
    for cfg in ("c1", "c2", "c3"):
        _scorecard(r, cfg, {"A": True, "B": False})   # A easy, B hard — both predicted right
    rep = compute_generator_report(r, "s")
    assert rep.difficulty_agreement == 1.0
    assert set(rep.zero_discrimination_ids) == {"A", "B"}   # both unanimous
