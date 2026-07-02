"""Red-team prompt-injection probe set + honest detector self-test (review #12).

The point is not to claim the lexical injection check is airtight — it's to
exercise the tail with a real, technique-diverse probe set and MEASURE where the
detector fails, publishing that rather than hiding it.
"""

from __future__ import annotations

from ascore.metrics.redteam import (
    INJECTION_PROBES,
    INJECTION_TECHNIQUES,
    REDTEAM_INJECTION_SUITE_ID,
    evaluate_injection_detector,
    seed_redteam_injection_suite,
    technique_counts,
)
from ascore.registry.sqlite_store import Registry


class TestProbeSet:
    def test_covers_many_techniques_not_one_shape(self):
        counts = technique_counts()
        # a genuine tail: many distinct injection techniques, not a couple
        assert len(counts) >= 10
        assert len(INJECTION_PROBES) >= 25
        # every probe's technique is documented
        for p in INJECTION_PROBES:
            assert p.technique in INJECTION_TECHNIQUES

    def test_has_benign_controls_for_false_positive_measurement(self):
        controls = [p for p in INJECTION_PROBES if p.is_control]
        assert len(controls) >= 3

    def test_seeds_as_a_real_runnable_suite(self, tmp_path):
        reg = Registry(tmp_path / "a.db")
        added = seed_redteam_injection_suite(reg)
        assert added == [REDTEAM_INJECTION_SUITE_ID]
        suite, cases = reg.get_suite(REDTEAM_INJECTION_SUITE_ID)
        assert len(cases) == len(INJECTION_PROBES)
        # idempotent
        assert seed_redteam_injection_suite(reg) == []


class TestDetectorSelfTest:
    def test_reports_confusion_matrix_and_rates(self):
        r = evaluate_injection_detector()
        c = r.to_dict()["confusion"]
        assert c["tp"] + c["fn"] + c["tn"] + c["fp"] == r.n
        assert 0.0 <= r.detection_rate <= 1.0
        assert 0.0 <= r.false_positive_rate <= 1.0

    def test_surfaces_the_missed_tail_honestly(self):
        # The lexical check genuinely misses evasions/exfiltration — that must be
        # reported (this is the whole honesty point), not swept away.
        r = evaluate_injection_detector()
        assert r.false_neg >= 3, "evasion tail must be present and reported"
        assert r.missed, "missed hijacks must be listed"
        # detection is demonstrably imperfect (heuristic, not the real env)
        assert r.detection_rate < 1.0

    def test_catches_the_obvious_hijacks(self):
        # It's not useless either — clear tool-call/emit hijacks are caught.
        r = evaluate_injection_detector()
        assert r.true_pos >= 3


class TestPublicEndpoint:
    def test_public_redteam_endpoint(self, tmp_path):
        from fastapi.testclient import TestClient

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
            r = c.get("/api/public/redteam/injection")   # no auth
            assert r.status_code == 200
            body = r.json()
            assert body["n_probes"] >= 25
            st = body["detector_self_test"]
            assert st["confusion"]["fn"] >= 3        # the honest missed tail
            assert "missed_hijacks" in st
