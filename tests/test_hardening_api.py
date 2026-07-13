"""Hardening endpoints over HTTP: a scorecard with failures becomes a promotion
candidate, promoting it creates a regression suite, and re-running it (with the
injected fake agent/judge) produces a new scorecard whose delta is reported on
the suite detail. Reuses the app+fakes fixture shared by the API suite."""

import time

from agenttic.hardening import regression_suite_id
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard

from tests.test_api import client  # noqa: F401 — reuse the app+fakes fixture


def _seed_failing_card(reg, *, scorecard_id="sc-fail", agent_id="router"):
    """Persist a pilot scorecard where two cases fail and one errors."""
    runs = [
        RunScore(trace_id="t0", test_id="triage-000", passed=False,
                 criterion_scores=[CriterionScore(criterion_id="accuracy",
                                                   score=0.0, scorer="code")]),
        RunScore(trace_id="t1", test_id="triage-001", passed=False,
                 criterion_scores=[CriterionScore(criterion_id="accuracy",
                                                   score=0.0, scorer="code")]),
        RunScore(trace_id="t2", test_id="triage-002", passed=True,
                 criterion_scores=[CriterionScore(criterion_id="accuracy",
                                                   score=1.0, scorer="code")]),
        RunScore(trace_id="t3", test_id="triage-003", passed=False,
                 criterion_scores=[], scoring_error="JudgeTimeout"),  # errored != failed
    ]
    sc = Scorecard.aggregate(
        scorecard_id=scorecard_id, agent_id=agent_id,
        suite_id="pilot-support-triage", suite_version=1,
        rubric_id="r-triage", rubric_version=1, run_scores=runs,
        visibility_tier="glass_box")
    reg.save_scorecard(sc)
    return sc


class TestHardeningAPI:
    def test_candidates_promote_list_detail(self, client):
        _seed_failing_card(client.reg)

        cands = client.get("/api/hardening/candidates").json()["candidates"]
        mine = next(c for c in cands if c["scorecard_id"] == "sc-fail")
        assert mine["n_failing"] == 2          # triage-000, triage-001
        assert mine["n_errored"] == 1          # triage-003 errored, excluded

        res = client.post("/api/hardening/promote",
                          json={"scorecard_id": "sc-fail"}).json()
        reg_id = regression_suite_id("router", "pilot-support-triage")
        assert res["regression_suite_id"] == reg_id
        assert sorted(res["added"]) == ["triage-000", "triage-001"]
        assert res["excluded_errored"] == ["triage-003"]

        suites = client.get("/api/hardening/suites").json()["suites"]
        assert any(s["regression_suite_id"] == reg_id for s in suites)

        detail = client.get(f"/api/hardening/suites/{reg_id}").json()
        assert sorted(c["test_id"] for c in detail["cases"]) == \
            ["triage-000", "triage-001"]
        assert detail["cases"][0]["provenance"]["why"]
        assert detail["history"] == []

    def test_rerun_produces_scorecard_and_delta(self, client):
        _seed_failing_card(client.reg)
        promo = client.post("/api/hardening/promote",
                            json={"scorecard_id": "sc-fail"}).json()
        reg_id = promo["regression_suite_id"]

        r = client.post("/api/hardening/rerun",
                        json={"regression_suite_id": reg_id})
        assert r.status_code == 200 and r.json()["started"] is True

        # poll the detail until the re-run lands a scorecard in history
        deadline = time.time() + 20.0
        detail = None
        while time.time() < deadline:
            detail = client.get(f"/api/hardening/suites/{reg_id}").json()
            if detail["history"]:
                break
            time.sleep(0.1)
        assert detail and detail["history"], "re-run produced no scorecard"
        # a delta is computed; first re-run has no prior regression card -> all "new"
        assert detail["latest_delta"]["summary"]["new"] >= 1

    def test_promote_unknown_scorecard_404(self, client):
        r = client.post("/api/hardening/promote", json={"scorecard_id": "nope"})
        assert r.status_code == 404
