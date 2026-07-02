"""Demonstrated calibration — the shipped human-label corpus turns "calibrated"
from an unproven claim into a reproducible number (review #11 / SPEC Hard Rule 6).

Also checks the Hard-Rule-6 wiring: judge criteria and non-demonstrated heuristic
checks are marked PROVISIONAL, while corpus-demonstrated checks are calibrated.
"""

from __future__ import annotations

from ascore.scoring.corpus import (
    HEURISTIC_CRITERIA,
    demonstrated_calibrated,
    run_corpus_calibration,
    uncalibrated_criteria,
)


class TestCorpusCalibration:
    def test_runs_offline_and_demonstrates_agreement(self):
        # No API key, no DB — the corpus + real checks produce a genuine number.
        result = run_corpus_calibration()
        assert result.n_records >= 20
        # a real, non-trivial agreement (not a rigged 1.0, not garbage)
        assert 0.8 <= result.overall_agreement < 1.0

    def test_each_covered_criterion_reports_n_and_agreement(self):
        result = run_corpus_calibration()
        for cid, cal in result.per_criterion.items():
            assert cid in HEURISTIC_CRITERIA
            assert cal.n >= 5           # enough labeled pairs to judge
            assert 0.0 <= cal.agreement <= 1.0

    def test_tail_disagreements_are_present_and_honest(self):
        # The lexical checks genuinely miss some cases; we surface that, not hide
        # it. Every disagreement is a check/human mismatch on a real record.
        result = run_corpus_calibration()
        assert result.disagreements, "seed corpus should expose real tail misses"
        for d in result.disagreements:
            assert d["check_score"] != d["human_score"]

    def test_demonstrated_set_is_the_calibrated_criteria(self):
        result = run_corpus_calibration()
        assert demonstrated_calibrated() == result.calibrated_criteria
        # the strong safety proxies clear the bar on this seed corpus
        assert {"harmful_action_refused", "injection_robust",
                "no_secret_leak"} <= demonstrated_calibrated()


class TestHardRule6Wiring:
    def test_judge_criteria_are_always_provisional(self):
        uncal = uncalibrated_criteria(
            ["tone", "harmful_action_refused"], {"tone": "judge",
                                                 "harmful_action_refused": "code"})
        assert "tone" in uncal  # judge calibration not demonstrated

    def test_demonstrated_heuristic_is_calibrated_pure_check_untouched(self):
        uncal = uncalibrated_criteria(
            ["harmful_action_refused", "tool_selection_accuracy"],
            {"harmful_action_refused": "code", "tool_selection_accuracy": "code"})
        # demonstrated heuristic -> calibrated; pure deterministic -> calibrated
        assert uncal == set()

    def test_undemonstrated_heuristic_is_provisional(self):
        # tool_misuse_safety is a heuristic check NOT covered by the seed corpus
        uncal = uncalibrated_criteria(
            ["tool_misuse_safety"], {"tool_misuse_safety": "code"})
        assert uncal == {"tool_misuse_safety"}


class TestPublicEndpoint:
    def test_public_calibration_endpoint(self, tmp_path):
        from fastapi.testclient import TestClient

        from ascore.registry.sqlite_store import Registry
        from ascore.server.app import create_app

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "models: {agent_default: a, judge_strong: j, judge_light: l}\n"
            "harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1,"
            " max_steps: 10}\n"
            "scoring: {calibration_threshold: 0.8}\n"
            "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
            f"paths: {{registry_db: {tmp_path}/a.db, review_dir: {tmp_path}/r,"
            f" calibration_dir: {tmp_path}/c}}\n"
            "auth: {token: adm, required: true, allow_signup: true,"
            " signup_role: operator, session_secret: testsecret}\n")
        reg = Registry(tmp_path / "a.db")
        with TestClient(create_app(str(cfg_path), registry=reg)) as c:
            r = c.get("/api/public/calibration")   # no auth
            assert r.status_code == 200
            body = r.json()
            assert body["overall_agreement"] >= 0.8
            assert "harmful_action_refused" in body["calibrated_criteria"]
            assert "judge" in body["note"].lower()
