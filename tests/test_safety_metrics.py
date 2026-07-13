"""Safety metric family — catalog wiring, seed suite, and Issues surfacing.

Complements ``test_safety_checks.py`` (detectors + checks) by verifying the
family is a first-class part of the catalog, that its seed suite validates and
seeds idempotently, and that a failing safety check surfaces as a ranked issue.
"""

from __future__ import annotations

from agenttic.issues import build_issues
from agenttic.metrics.catalog import BY_ID, CHECK_TO_METRIC, catalog_payload, index_weights
from agenttic.metrics.safety_catalog import SAFETY_METRICS, safety_metric_payload
from agenttic.metrics.safety_suite import (
    SAFETY_CONTENT_SUITE_ID,
    _build,
    seed_safety_content_suite,
)
from agenttic.scoring.checks import validate_rubric_checks


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #

class TestCatalog:
    def test_family_present_in_catalog(self):
        ids = {m["id"] for m in catalog_payload()}
        for mid in ("pii_leakage", "secret_leakage", "profanity",
                    "system_prompt_leak", "toxicity", "bias_fairness",
                    "unsafe_content"):
            assert mid in ids, mid
            assert BY_ID[mid].category == "safety"

    def test_family_is_unweighted_and_out_of_index(self):
        # recall-bounded / provisional => weight 0 and NOT in the Agenttic Index
        weighted = set(index_weights())
        for m in SAFETY_METRICS:
            assert m.weight == 0.0
            assert m.id not in weighted

    def test_judge_metrics_are_provisional(self):
        for mid in ("toxicity", "bias_fairness", "unsafe_content"):
            assert BY_ID[mid].status == "provisional"

    def test_deterministic_metrics_implemented(self):
        for mid in ("pii_leakage", "secret_leakage", "profanity",
                    "system_prompt_leak"):
            assert BY_ID[mid].status == "implemented"

    def test_check_rollup(self):
        assert CHECK_TO_METRIC["no_pii_leak"] == "pii_leakage"
        assert CHECK_TO_METRIC["no_secret_disclosure"] == "secret_leakage"
        assert CHECK_TO_METRIC["no_profanity"] == "profanity"

    def test_payload_helper(self):
        pl = safety_metric_payload()
        assert len(pl) == len(SAFETY_METRICS)
        assert all("methodology" in p and "status" in p for p in pl)


# --------------------------------------------------------------------------- #
# Seed suite
# --------------------------------------------------------------------------- #

class TestSafetySuite:
    def test_rubrics_reference_registered_checks(self):
        spec = _build()
        for rubric in spec.rubrics:
            validate_rubric_checks(rubric)  # code criteria must resolve

    def test_every_case_has_a_rubric(self):
        spec = _build()
        rubric_ids = {r.rubric_id for r in spec.rubrics}
        assert spec.cases
        for c in spec.cases:
            assert c.rubric_id in rubric_ids

    def test_seed_is_idempotent(self, tmp_path):
        from agenttic.registry.sqlite_store import Registry

        reg = Registry(tmp_path / "safety.db")
        first = seed_safety_content_suite(reg)
        assert first == [SAFETY_CONTENT_SUITE_ID]
        # suite is retrievable, with all 8 cases
        suite, cases = reg.get_suite(SAFETY_CONTENT_SUITE_ID)
        assert suite.suite_id == SAFETY_CONTENT_SUITE_ID
        assert len(cases) == 8
        # second call is a no-op
        assert seed_safety_content_suite(reg) == []


# --------------------------------------------------------------------------- #
# Issues surfacing
# --------------------------------------------------------------------------- #

def _case(test_id, criteria, *, prediction="out"):
    return {"node_id": "score", "test_id": test_id,
            "passed": all((c.get("score") or 0) >= 1 for c in criteria),
            "scoring_error": None, "prediction": prediction, "expected": None,
            "criteria": criteria}


def _crit(cid, score, *, calibrated=True, rationale=None):
    return {"criterion_id": cid, "score": score, "scorer": "code",
            "calibrated": calibrated, "rationale": rationale}


class TestIssuesSurfacing:
    def test_pii_leak_surfaces_as_safety_issue(self):
        cases = [_case("t1", [_crit("no_pii_leak", 0.0)]),
                 _case("t2", [_crit("no_pii_leak", 1.0)])]
        meta = {"no_pii_leak": {"description": "Does not disclose protected PII",
                                "check_ref": "no_pii_leak", "tags": ["safety"]}}
        rep = build_issues(scorecards=[], cases=cases, criteria_meta=meta)
        pii = [i for i in rep["issues"] if i["criterion_id"] == "no_pii_leak"]
        assert pii and pii[0]["category"] == "safety"
        assert pii[0]["severity"] in ("critical", "high", "medium")

    def test_secret_and_profanity_categorized_safety(self):
        cases = [_case("t1", [_crit("no_secret_disclosure", 0.0),
                              _crit("no_profanity", 0.0)])]
        meta = {
            "no_secret_disclosure": {"description": "Does not leak secrets",
                                     "check_ref": "no_secret_disclosure"},
            "no_profanity": {"description": "No profanity", "check_ref": "no_profanity"},
        }
        rep = build_issues(scorecards=[], cases=cases, criteria_meta=meta)
        cats = {i["criterion_id"]: i["category"] for i in rep["issues"]}
        assert cats["no_secret_disclosure"] == "safety"
        assert cats["no_profanity"] == "safety"
