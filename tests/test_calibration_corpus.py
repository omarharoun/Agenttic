"""Demonstrated calibration — the shipped human-label corpus turns "calibrated"
from an unproven claim into a reproducible number (review #11 / SPEC Hard Rule 6).

Also checks the Hard-Rule-6 wiring: judge criteria and non-demonstrated heuristic
checks are marked PROVISIONAL, while corpus-demonstrated checks are calibrated.
"""

from __future__ import annotations

from agenttic.scoring.corpus import (
    HEURISTIC_CRITERIA,
    demonstrated_calibrated,
    run_corpus_calibration,
    uncalibrated_criteria,
)


class TestCorpusCalibration:
    def test_runs_offline_and_demonstrates_agreement(self):
        # No API key, no DB — the corpus + real checks produce a genuine number.
        result = run_corpus_calibration()
        assert result.n_records >= 50          # expanded, genuinely-curated set
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
        # These clear the bar on the expanded corpus; tool_misuse_safety is newly
        # demonstrated (was uncovered). injection_robust honestly does NOT clear
        # it on the harder sample (paraphrase / non-English tails) and stays
        # provisional — the honest Hard Rule 6 outcome, not tuned away.
        cal = demonstrated_calibrated()
        assert {"harmful_action_refused", "no_secret_leak",
                "faithfulness_grounded", "tool_misuse_safety"} <= cal
        assert "injection_robust" not in cal

    def test_tool_misuse_safety_now_demonstrated(self):
        # Expanding the corpus moved tool_misuse_safety from uncovered/provisional
        # to demonstrated-calibrated.
        result = run_corpus_calibration()
        tms = result.per_criterion.get("tool_misuse_safety")
        assert tms is not None and tms.n >= 8 and tms.calibrated is True


class TestHardRule6Wiring:
    def test_undemonstrated_judge_criteria_are_provisional(self):
        uncal = uncalibrated_criteria(
            ["tone", "harmful_action_refused"], {"tone": "judge",
                                                 "harmful_action_refused": "code"})
        assert "tone" in uncal  # this judge criterion has no demonstrated run

    def test_recorded_judge_run_does_not_promote_criteria(self):
        # Corrected honest semantics (Hard Rule 6): the small recorded judge run
        # (n=5, clear-cut) is NOT a qualifying calibration study, so it must NOT
        # promote these judge criteria out of PROVISIONAL — a hardcoded record
        # can't silently lift a tier. They all stay provisional.
        uncal = uncalibrated_criteria(
            ["helpfulness", "faithfulness_judge", "tone_professional"],
            {"helpfulness": "judge", "faithfulness_judge": "judge",
             "tone_professional": "judge"})
        assert uncal == {"helpfulness", "faithfulness_judge", "tone_professional"}

    def test_demonstrated_heuristic_is_calibrated_pure_check_untouched(self):
        uncal = uncalibrated_criteria(
            ["harmful_action_refused", "tool_selection_accuracy"],
            {"harmful_action_refused": "code", "tool_selection_accuracy": "code"})
        # demonstrated heuristic -> calibrated; pure deterministic -> calibrated
        assert uncal == set()

    def test_undemonstrated_heuristic_is_provisional(self):
        # injection_robust is a heuristic check that does NOT clear the agreement
        # bar on the expanded corpus -> stays provisional.
        uncal = uncalibrated_criteria(
            ["injection_robust"], {"injection_robust": "code"})
        assert uncal == {"injection_robust"}


class TestPublicEndpoint:
    def test_public_calibration_endpoint(self, tmp_path):
        from fastapi.testclient import TestClient

        from agenttic.registry.sqlite_store import Registry
        from agenttic.server.app import create_app

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
