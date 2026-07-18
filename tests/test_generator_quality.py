"""Step 16 — generator scoring (measure before optimizing).

Synthetic suite with KNOWN answers, no network:
* the approve flow records the review diff (added/edited/deleted vs snapshot);
* compute_generator_report yields all three metrics with exact, hand-checkable
  values;
* honest degradation: <2 agent configs => discrimination None; no snapshot =>
  edit_rate None.
"""

from __future__ import annotations

from agenttic.generator.quality import compute_generator_report
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.scorecard import RunScore, Scorecard
from agenttic.schema.testcase import TestCase, TestSuite


def _case(i, *, tags, expected=None, input=None):
    return TestCase(
        test_id=f"s-{i:03d}", suite_id="s", version=1,
        task_description=f"case {i}",
        input=input or {"x": i}, expected=expected or {"final_output": f"a{i}"},
        tags=tags, rubric_id="s-r",
    )


def _suite(test_ids, approved=False):
    return TestSuite(suite_id="s", version=1, business_context="ctx",
                     test_ids=test_ids, approved=approved)


def _scorecard(reg, agent_id, verdicts, *, errors=()):
    """verdicts: {test_id: passed_bool}. errors: iterable of test_ids scored
    with a scoring_error (excluded from quality aggregates)."""
    runs = []
    for tid, passed in verdicts.items():
        runs.append(RunScore(trace_id=f"{agent_id}-{tid}", test_id=tid,
                             criterion_scores=[], passed=passed))
    for tid in errors:
        runs.append(RunScore(trace_id=f"{agent_id}-{tid}-e", test_id=tid,
                             criterion_scores=[], passed=False,
                             scoring_error="judge outage"))
    sc = Scorecard.aggregate(
        scorecard_id=f"sc-{agent_id}", agent_id=agent_id, suite_id="s",
        suite_version=1, rubric_id="s-r", rubric_version=1,
        run_scores=runs, visibility_tier="glass_box")
    reg.save_scorecard(sc)
    return sc


# -- 1. approve flow records the review diff --------------------------------

def test_approve_records_review_diff(tmp_path):
    reg = Registry(tmp_path / "db.sqlite")
    generated = [_case(0, tags=["happy_path"]), _case(1, tags=["edge_case"]),
                 _case(2, tags=["adversarial"])]
    # Save the DRAFT suite + snapshot the as-generated set.
    reg.save_suite(_suite([c.test_id for c in generated]), generated)
    reg.save_generated_snapshot("s", 1, generated)

    # Human review: edit case 1 (change expected), delete case 2, add a new case.
    edited = _case(1, tags=["edge_case"], expected={"final_output": "CHANGED"})
    added = _case(9, tags=["happy_path"])
    approved_cases = [generated[0], edited, added]
    # Re-store as the approved (current) version. save_suite refuses overwrite,
    # so replace the case rows directly via a fresh version-1 image: simplest is
    # a new registry op — here we emulate the human edit by replacing cases.
    _replace_cases(reg, approved_cases)

    reg.approve_suite("s", 1)

    diff = reg.get_review_diff("s", 1)
    assert diff is not None
    assert diff["generated_count"] == 3
    assert diff["added"] == 1 and diff["added_ids"] == ["s-009"]
    assert diff["edited"] == 1 and diff["edited_ids"] == ["s-001"]
    assert diff["deleted"] == 1 and diff["deleted_ids"] == ["s-002"]
    assert diff["unchanged"] == 1  # case 0 untouched


def _replace_cases(reg, cases):
    """Replace the stored v1 case rows for suite 's' with the given set (models
    the human editing the draft in place during review)."""
    from sqlmodel import Session, delete, select
    from agenttic.registry.sqlite_store import CaseRow, SuiteRow
    with Session(reg.engine) as s:
        s.exec(delete(CaseRow).where(CaseRow.tenant_id == reg.tenant,
                                     CaseRow.suite_id == "s"))
        for c in cases:
            s.add(CaseRow(tenant_id=reg.tenant, suite_id="s", suite_version=1,
                          test_id=c.test_id, payload=c.model_dump_json()))
        # keep the SuiteRow payload's test_ids consistent
        row = s.exec(select(SuiteRow).where(SuiteRow.tenant_id == reg.tenant,
                                            SuiteRow.suite_id == "s")).first()
        suite = TestSuite.model_validate_json(row.payload)
        suite.test_ids = [c.test_id for c in cases]
        row.payload = suite.model_dump_json()
        s.add(row)
        s.commit()


# -- 2. report computes all three metrics on known data ---------------------

def test_report_all_three_metrics(tmp_path):
    reg = Registry(tmp_path / "db.sqlite")
    # Coverage: 2 happy_path, 1 edge_case, 1 adversarial (TAG_MIX target is
    # 2/5, 2/5, 1/5).
    cases = [_case(0, tags=["happy_path"]), _case(1, tags=["happy_path"]),
             _case(2, tags=["edge_case"]), _case(3, tags=["adversarial"])]
    reg.save_suite(_suite([c.test_id for c in cases]), cases)
    reg.save_generated_snapshot("s", 1, cases)

    # Edit exactly one case (case 3), delete none, add none => edit_rate = 1/4.
    edited = _case(3, tags=["adversarial"], expected={"final_output": "X"})
    _replace_cases(reg, [cases[0], cases[1], cases[2], edited])
    reg.approve_suite("s", 1)

    # Two agent configs:
    #   case 0: passed by BOTH  -> discriminates nothing (0)
    #   case 1: FAILED by BOTH  -> discriminates nothing (0)
    #   case 2: mixed (A pass, B fail) -> discriminates (1)
    #   case 3: mixed (A fail, B pass) -> discriminates (1)
    _scorecard(reg, "agentA", {"s-000": True, "s-001": False,
                               "s-002": True, "s-003": False})
    _scorecard(reg, "agentB", {"s-000": True, "s-001": False,
                               "s-002": False, "s-003": True})

    rep = compute_generator_report(reg, "s")

    # edit_rate = (edited + deleted)/generated = (1 + 0)/4
    assert rep.edit_rate == 0.25
    # discrimination = 2 mixed / 4 measurable cases
    assert rep.discrimination == 0.5
    assert rep.n_agent_configs == 2
    assert rep.n_cases == 4
    # coverage balance vs TAG_MIX (2/5, 2/5, 1/5)
    cov = rep.coverage_balance
    assert cov["actual"]["happy_path"] == 0.5   # 2/4
    assert cov["actual"]["edge_case"] == 0.25    # 1/4
    assert cov["actual"]["adversarial"] == 0.25  # 1/4
    assert cov["target"]["happy_path"] == 0.4    # 2/5
    assert cov["deltas"]["happy_path"] == 0.1    # 0.5 - 0.4


def test_all_pass_and_all_fail_contribute_zero(tmp_path):
    reg = Registry(tmp_path / "db.sqlite")
    cases = [_case(0, tags=["happy_path"]), _case(1, tags=["edge_case"])]
    reg.save_suite(_suite([c.test_id for c in cases]), cases)
    # case 0 passed by both, case 1 failed by both => discrimination 0/2 = 0.0
    _scorecard(reg, "agentA", {"s-000": True, "s-001": False})
    _scorecard(reg, "agentB", {"s-000": True, "s-001": False})
    rep = compute_generator_report(reg, "s")
    assert rep.discrimination == 0.0


def test_scoring_errors_excluded_from_discrimination(tmp_path):
    reg = Registry(tmp_path / "db.sqlite")
    cases = [_case(0, tags=["happy_path"]), _case(1, tags=["edge_case"])]
    reg.save_suite(_suite([c.test_id for c in cases]), cases)
    # case 0 is mixed; case 1 has a scoring error on agentB => only one verdict,
    # so it is not measurable and doesn't dilute the fraction.
    _scorecard(reg, "agentA", {"s-000": True, "s-001": True})
    _scorecard(reg, "agentB", {"s-000": False}, errors=["s-001"])
    rep = compute_generator_report(reg, "s")
    assert rep.discrimination == 1.0  # 1 discriminating / 1 measurable


# -- 3. honest degradation --------------------------------------------------

def test_single_agent_config_discrimination_none(tmp_path):
    reg = Registry(tmp_path / "db.sqlite")
    cases = [_case(0, tags=["happy_path"])]
    reg.save_suite(_suite([c.test_id for c in cases]), cases)
    _scorecard(reg, "agentA", {"s-000": True})
    rep = compute_generator_report(reg, "s")
    assert rep.discrimination is None
    assert rep.n_agent_configs == 1
    assert any("agent config" in n for n in rep.notes)


def test_no_snapshot_edit_rate_none(tmp_path):
    reg = Registry(tmp_path / "db.sqlite")
    cases = [_case(0, tags=["happy_path"])]
    # No snapshot saved (models a mined/imported suite).
    reg.save_suite(_suite([c.test_id for c in cases]), cases)
    reg.approve_suite("s", 1)  # must not raise despite absent snapshot
    rep = compute_generator_report(reg, "s")
    assert rep.edit_rate is None
    assert any("snapshot" in n for n in rep.notes)


def test_approve_never_blocked_by_diff_failure(tmp_path):
    reg = Registry(tmp_path / "db.sqlite")
    cases = [_case(0, tags=["happy_path"])]
    reg.save_suite(_suite([c.test_id for c in cases]), cases)
    reg.save_generated_snapshot("s", 1, cases)
    reg.approve_suite("s", 1)
    suite, _ = reg.get_suite("s")
    assert suite.approved is True
    # unchanged case => edit_rate 0.0
    rep = compute_generator_report(reg, "s")
    assert rep.edit_rate == 0.0
