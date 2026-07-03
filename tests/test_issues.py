"""Issues report — pure builder + the /executions/{id}/issues endpoint.

Covers category inference, severity/impact ranking (safety > reliability >
efficiency), the plain-language why + evidence, the honest "no issues found"
path, provisional-judge + scoring-error aggregate issues, and the end-to-end
endpoint over a real pilot execution (0.8 success ⇒ real failures).
"""

from __future__ import annotations

from ascore.issues import build_issues, categorize_criterion
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_executor import eval_workflow


# --------------------------------------------------------------------------- #
# Pure builder
# --------------------------------------------------------------------------- #

def _case(test_id, criteria, *, passed=None, prediction="out", expected=None,
          scoring_error=None):
    if passed is None:
        passed = all((c.get("score") or 0) >= 1 for c in criteria)
    return {"node_id": "score", "test_id": test_id, "passed": passed,
            "scoring_error": scoring_error, "prediction": prediction,
            "expected": expected, "criteria": criteria}


def _crit(cid, score, *, scorer="code", calibrated=True, rationale=None):
    return {"criterion_id": cid, "score": score, "scorer": scorer,
            "calibrated": calibrated, "rationale": rationale}


class TestCategorize:
    def test_known_safety_dimensions(self):
        assert categorize_criterion("harmful_action_refused", {}) == "safety"
        assert categorize_criterion("injection_robust", {}) == "prompt-injection"
        assert categorize_criterion("tool_misuse_safety", {}) == "tool-use"

    def test_inferred_from_metadata(self):
        assert categorize_criterion(
            "used_kb", {"check_ref": "required_tool_called"}) == "tool-use"
        assert categorize_criterion(
            "step_budget", {"description": "within the step budget"}) == "efficiency"
        assert categorize_criterion(
            "routing", {"description": "routed to the correct queue"}) == "reliability"
        assert categorize_criterion(
            "guard", {"description": "resists prompt injection"}) == "prompt-injection"


class TestBuildIssues:
    def test_no_failures_is_honest(self):
        cases = [_case("t1", [_crit("routing", 1.0)]),
                 _case("t2", [_crit("routing", 1.0)])]
        rep = build_issues(scorecards=[], cases=cases, criteria_meta={})
        assert rep["issues"] == []
        assert rep["summary"]["clean"] is True
        assert "No issues" in rep["summary"]["headline"]

    def test_ranks_safety_above_efficiency(self):
        # safety fails on 2/2, efficiency fails on 1/2 → safety ranks first
        cases = [
            _case("t1", [_crit("harmful_action_refused", 0.0),
                         _crit("step_budget", 0.0)]),
            _case("t2", [_crit("harmful_action_refused", 0.0),
                         _crit("step_budget", 1.0)]),
        ]
        meta = {"step_budget": {"description": "within the step budget"}}
        rep = build_issues(scorecards=[], cases=cases, criteria_meta=meta)
        cats = [i["category"] for i in rep["issues"]]
        assert cats[0] == "safety"
        assert "efficiency" in cats
        top = rep["issues"][0]
        assert top["severity"] == "critical"          # critical dim, 100% fail
        assert top["affected_n"] == 2 and top["n_measured"] == 2
        assert top["suggested_fix"]["route"] == "/app/hardening"

    def test_evidence_and_why_carry_rationale(self):
        cases = [_case("t1", [_crit("tone", 0.0, scorer="judge",
                                    rationale="Mocked the customer.")],
                       prediction="lol whatever", expected={"final_output": "billing"})]
        meta = {"tone": {"description": "professional, neutral register",
                         "scorer": "judge"}}
        rep = build_issues(scorecards=[], cases=cases, criteria_meta=meta)
        issue = next(i for i in rep["issues"] if i["criterion_id"] == "tone")
        assert "Mocked the customer" in issue["why"]
        ev = issue["evidence"]["cases"][0]
        assert ev["test_id"] == "t1" and ev["rationale"] == "Mocked the customer."
        assert ev["prediction"] == "lol whatever"

    def test_scoring_errors_become_an_issue(self):
        cases = [_case("t1", [_crit("routing", 1.0)]),
                 _case("t2", [], scoring_error="TimeoutError: agent timed out")]
        rep = build_issues(scorecards=[], cases=cases, criteria_meta={})
        err = next(i for i in rep["issues"] if i["id"] == "errored-cases")
        assert err["affected_n"] == 1
        assert "timed out" in err["why"].lower()

    def test_provisional_judge_issue(self):
        cases = [_case("t1", [_crit("tone", 1.0, scorer="judge", calibrated=False)])]
        rep = build_issues(scorecards=[], cases=cases,
                           criteria_meta={"tone": {"description": "tone"}})
        prov = next(i for i in rep["issues"] if i["id"] == "uncalibrated-judge")
        assert prov["category"] == "calibration"
        assert prov["severity"] == "low"


# --------------------------------------------------------------------------- #
# Endpoint (end-to-end over a real pilot execution)
# --------------------------------------------------------------------------- #

def _run_pilot(client):  # noqa: F811 — reuses the imported `client` app fixture
    wf = eval_workflow("pilot-support-triage").model_dump()
    client.post("/api/workflows", json=wf)
    eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
    import time
    deadline = time.time() + 15
    while time.time() < deadline:
        if client.get(f"/api/executions/{eid}").json()["status"] == "succeeded":
            return eid
        time.sleep(0.05)
    raise AssertionError("pilot never succeeded")


class TestIssuesEndpoint:
    def test_pilot_execution_has_ranked_issues(self, client):  # noqa: F811
        eid = _run_pilot(client)
        rep = client.get(f"/api/executions/{eid}/issues").json()
        assert rep["status"] == "succeeded"
        assert rep["summary"]["total_issues"] >= 1
        # the pilot fails some cases (0.8 success) → at least one criterion issue
        assert any(i["criterion_id"] for i in rep["issues"])
        # ranked worst-first by severity rank
        from ascore.issues import SEVERITY_RANK
        ranks = [SEVERITY_RANK[i["severity"]] for i in rep["issues"]]
        assert ranks == sorted(ranks, reverse=True)
        # every issue carries a fix + real evidence
        for i in rep["issues"]:
            assert i["suggested_fix"]["route"].startswith("/")
            assert i["why"]
        # honest headline reflects the count
        assert str(rep["summary"]["total_issues"]) in rep["summary"]["headline"]

    def test_missing_execution_404(self, client):  # noqa: F811
        assert client.get("/api/executions/nope/issues").status_code == 404
