"""Failure-to-benchmark hardening loop:

- promoting a scorecard's failing cases creates a regression suite (v1);
- a second promotion appends + version-bumps (append-only), de-duping cases
  already promoted and near-identical (same content, different test_id) cases;
- errored cases are excluded from promotion (errored != failed);
- the per-case regression delta classifies improved / regressed / same / new
  and excludes errored cases;
- re-running a regression suite produces a delta vs the prior regression
  scorecard (wired through the real registry; scoring stubbed for determinism).
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agenttic import hardening, ops
from agenttic.registry.sqlite_store import NotFoundError, Registry
from agenttic.schema.rubric import Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.testcase import TestCase, TestSuite

PILOT = Path(__file__).parent.parent / "examples" / "pilot_support_triage"

CFG = {
    "models": {"agent_default": "agent-model", "judge_strong": "judge-model",
               "judge_light": "judge-light"},
    "harness": {"timeout_seconds": 10, "max_parallel": 5,
                "transport_retries": 1, "max_steps": 10},
    "scoring": {"calibration_threshold": 0.8},
    "paths": {"review_dir": "review/"},
    "budget": {},
    "security": {},
}


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "harden.db")


def _load_pilot(reg: Registry) -> tuple[str, list[TestCase]]:
    reg.save_rubric(Rubric.model_validate_json((PILOT / "rubric.json").read_text()))
    suite = TestSuite.model_validate_json((PILOT / "suite.json").read_text())
    cases = [TestCase.model_validate(c)
             for c in json.loads((PILOT / "cases.json").read_text())]
    reg.save_suite(suite, cases)
    reg.approve_suite(suite.suite_id, suite.version)
    return suite.suite_id, cases


def _run(test_id: str, passed: bool, *, error: str | None = None,
         crit: float | None = None) -> RunScore:
    scores = []
    if crit is not None:
        scores = [CriterionScore(criterion_id="accuracy", score=crit, scorer="code")]
    return RunScore(trace_id=f"tr-{test_id}", test_id=test_id,
                    criterion_scores=scores, passed=passed, scoring_error=error)


def _scorecard(reg: Registry, suite_id: str, runs: list[RunScore], *,
               agent_id: str = "router", scorecard_id: str = "sc-1",
               suite_version: int = 1, created_at: datetime | None = None) -> Scorecard:
    sc = Scorecard.aggregate(
        scorecard_id=scorecard_id, agent_id=agent_id, suite_id=suite_id,
        suite_version=suite_version, rubric_id="r-triage", rubric_version=1,
        run_scores=runs, visibility_tier="glass_box")
    if created_at is not None:
        sc = sc.model_copy(update={"created_at": created_at})
    reg.save_scorecard(sc)
    return sc


# -- capture / promote -------------------------------------------------------

class TestPromote:
    def test_promote_creates_regression_suite(self, reg):
        suite_id, _ = _load_pilot(reg)
        runs = [_run("triage-000", False, crit=0.0),
                _run("triage-001", False, crit=0.5),
                _run("triage-002", True, crit=1.0),
                _run("triage-003", True, crit=1.0)]
        _scorecard(reg, suite_id, runs)

        res = hardening.promote_failures_op(reg, "sc-1")
        assert res["created"] is True
        assert res["version"] == 1
        assert sorted(res["added"]) == ["triage-000", "triage-001"]

        reg_id = hardening.regression_suite_id("router", suite_id)
        assert res["regression_suite_id"] == reg_id
        suite, cases = reg.get_suite(reg_id)
        assert suite.approved is True  # runnable immediately
        assert sorted(c.test_id for c in cases) == ["triage-000", "triage-001"]
        # every promoted case is re-homed onto the regression suite
        assert all(c.suite_id == reg_id for c in cases)
        # provenance: the original "why it failed" is preserved
        manifest = hardening._decode_manifest(suite.business_context)
        assert manifest["cases"]["triage-000"]["why"].startswith("failed criteria")
        assert manifest["cases"]["triage-000"]["source_scorecard_id"] == "sc-1"

    def test_errored_cases_excluded(self, reg):
        suite_id, _ = _load_pilot(reg)
        runs = [_run("triage-000", False, crit=0.0),
                _run("triage-001", False, error="JudgeTimeout: boom")]
        _scorecard(reg, suite_id, runs)

        res = hardening.promote_failures_op(reg, "sc-1")
        # errored case is not a failure: excluded from promotion, surfaced apart
        assert res["added"] == ["triage-000"]
        assert res["excluded_errored"] == ["triage-001"]
        _, cases = reg.get_suite(res["regression_suite_id"])
        assert [c.test_id for c in cases] == ["triage-000"]

    def test_append_bumps_version_and_dedupes(self, reg):
        suite_id, _ = _load_pilot(reg)
        _scorecard(reg, suite_id, [_run("triage-000", False, crit=0.0)],
                   scorecard_id="sc-1")
        first = hardening.promote_failures_op(reg, "sc-1")
        assert first["version"] == 1

        # second card: triage-000 fails again (dup) + triage-004 is new
        _scorecard(reg, suite_id,
                   [_run("triage-000", False, crit=0.0),
                    _run("triage-004", False, crit=0.0)],
                   scorecard_id="sc-2")
        second = hardening.promote_failures_op(reg, "sc-2")
        assert second["version"] == 2          # append-only bump
        assert second["added"] == ["triage-004"]
        assert "triage-000" in second["skipped_duplicates"]  # already promoted

        # v1 is preserved (append-only); v2 holds both cases
        _, v1_cases = reg.get_suite(second["regression_suite_id"], 1)
        assert [c.test_id for c in v1_cases] == ["triage-000"]
        _, v2_cases = reg.get_suite(second["regression_suite_id"], 2)
        assert sorted(c.test_id for c in v2_cases) == ["triage-000", "triage-004"]

    def test_nothing_new_is_idempotent(self, reg):
        suite_id, _ = _load_pilot(reg)
        _scorecard(reg, suite_id, [_run("triage-000", False, crit=0.0)])
        hardening.promote_failures_op(reg, "sc-1")
        again = hardening.promote_failures_op(reg, "sc-1")
        assert again["added"] == []
        assert again["version"] == 1           # no spurious version bump
        assert again["created"] is False

    def test_near_identical_cases_deduped(self, reg):
        # one source suite with two content-identical cases (different test_ids)
        reg.save_rubric(Rubric.model_validate_json((PILOT / "rubric.json").read_text()))
        common = {"task_description": "Handle a refund request",
                  "input": {"ticket": "please refund me"}, "rubric_id": "r-triage"}
        c_a = TestCase(test_id="a1", suite_id="s1", **common)
        c_b = TestCase(test_id="b1", suite_id="s1", **common)  # near-identical
        assert hardening.fingerprint(c_a) == hardening.fingerprint(c_b)
        suite = TestSuite(suite_id="s1", version=1, business_context="x",
                          test_ids=["a1", "b1"], approved=True)
        reg.save_suite(suite, [c_a, c_b])
        _scorecard(reg, "s1",
                   [_run("a1", False, crit=0.0), _run("b1", False, crit=0.0)],
                   scorecard_id="sc-1")

        res = hardening.promote_failures_op(reg, "sc-1")
        # both failed, but they are near-identical => only one is promoted
        assert res["added"] == ["a1"]
        assert "b1" in res["skipped_duplicates"]

    def test_explicit_subset(self, reg):
        suite_id, _ = _load_pilot(reg)
        _scorecard(reg, suite_id,
                   [_run("triage-000", False, crit=0.0),
                    _run("triage-001", False, crit=0.0),
                    _run("triage-002", False, crit=0.0)])
        res = hardening.promote_failures_op(reg, "sc-1", test_ids=["triage-001"])
        assert res["added"] == ["triage-001"]


# -- delta -------------------------------------------------------------------

class TestDelta:
    def _card(self, results: dict[str, bool | str]) -> Scorecard:
        runs = []
        for tid, r in results.items():
            if r == "error":
                runs.append(_run(tid, False, error="boom"))
            else:
                runs.append(_run(tid, bool(r), crit=1.0 if r else 0.0))
        return Scorecard.aggregate(
            scorecard_id=f"sc-{id(results)}", agent_id="router",
            suite_id="reg", suite_version=1, rubric_id="r-triage",
            rubric_version=1, run_scores=runs, visibility_tier="glass_box")

    def test_per_case_classification(self):
        prev = self._card({"a": False, "b": True, "c": True, "d": False})
        cur = self._card({"a": True,   # fail -> pass
                          "b": False,   # pass -> fail
                          "c": True,    # unchanged
                          "e": True,    # new case
                          "f": "error"})  # errored: excluded
        delta = hardening.compute_regression_delta(prev, cur)
        s = delta["summary"]
        assert s["improved"] == 1
        assert s["regressed"] == 1
        assert s["same"] == 1
        assert s["new"] == 1
        assert s["errored"] == 1
        by_id = {c["test_id"]: c["status"] for c in delta["per_case"]}
        assert by_id == {"a": "improved", "b": "regressed", "c": "same",
                         "e": "new", "f": "errored"}

    def test_no_prior_card_all_new(self):
        cur = self._card({"a": True, "b": False})
        delta = hardening.compute_regression_delta(None, cur)
        assert delta["summary"]["new"] == 2
        assert delta["prev_scorecard_id"] is None
        assert delta["success_delta"] is None

    def test_mcnemar_present_on_discordance(self):
        prev = self._card({"a": False, "b": False, "c": True})
        cur = self._card({"a": True, "b": True, "c": True})  # two improvements
        delta = hardening.compute_regression_delta(prev, cur)
        assert delta["mcnemar"] is not None
        assert delta["success_delta"] > 0


# -- re-run (wired through the registry; scoring stubbed) --------------------

class TestRerun:
    def test_rerun_reports_delta_vs_prior(self, reg, monkeypatch):
        suite_id, _ = _load_pilot(reg)
        _scorecard(reg, suite_id,
                   [_run("triage-000", False, crit=0.0),
                    _run("triage-001", False, crit=0.0)])
        promo = hardening.promote_failures_op(reg, "sc-1")
        reg_id = promo["regression_suite_id"]

        # seed a prior regression scorecard where both cases still fail
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        _scorecard(reg, reg_id,
                   [_run("triage-000", False, crit=0.0),
                    _run("triage-001", False, crit=0.0)],
                   scorecard_id="reg-prev", suite_version=promo["version"],
                   created_at=old)

        # stub scoring: the fix landed -> both cases now pass
        async def fake_run_and_score(cfg, reg_, adapter, suite_id_, **kw):
            runs = [_run("triage-000", True, crit=1.0),
                    _run("triage-001", True, crit=1.0)]
            sc = Scorecard.aggregate(
                scorecard_id="reg-new", agent_id=adapter.agent_id,
                suite_id=suite_id_, suite_version=promo["version"],
                rubric_id="r-triage", rubric_version=1, run_scores=runs,
                visibility_tier="glass_box")
            reg_.save_scorecard(sc)
            return sc
        monkeypatch.setattr(ops, "run_and_score_op", fake_run_and_score)

        out = asyncio.run(hardening.rerun_regression_op(
            CFG, reg, reg_id, client=object(), judge_client=object()))
        assert out["agent_id"] == "router"
        delta = out["delta"]
        assert delta["summary"]["improved"] == 2   # both fail -> pass
        assert delta["summary"]["regressed"] == 0
        assert delta["prev_scorecard_id"] == "reg-prev"
        assert delta["success_delta"] == pytest.approx(1.0)


# -- discovery surfaces ------------------------------------------------------

class TestDiscovery:
    def test_list_and_candidates_and_detail(self, reg):
        suite_id, _ = _load_pilot(reg)
        _scorecard(reg, suite_id,
                   [_run("triage-000", False, crit=0.0),
                    _run("triage-002", True, crit=1.0)])
        promo = hardening.promote_failures_op(reg, "sc-1")

        cands = hardening.promotion_candidates(reg)
        assert any(c["scorecard_id"] == "sc-1" and c["n_failing"] == 1
                   for c in cands)
        # the regression suite's own (future) cards must never be candidates
        assert all(not c["suite_id"].startswith(hardening.REGRESSION_PREFIX)
                   for c in cands)

        suites = hardening.list_regression_suites(reg)
        assert len(suites) == 1
        assert suites[0]["regression_suite_id"] == promo["regression_suite_id"]
        assert suites[0]["agent_id"] == "router"
        assert suites[0]["source_suite_id"] == suite_id

        detail = hardening.regression_detail(reg, promo["regression_suite_id"])
        assert [c["test_id"] for c in detail["cases"]] == ["triage-000"]
        assert detail["cases"][0]["provenance"]["why"]
        assert detail["history"] == []  # no re-run yet

    def test_detail_unknown_raises(self, reg):
        with pytest.raises(NotFoundError):
            hardening.regression_detail(reg, "nope")
