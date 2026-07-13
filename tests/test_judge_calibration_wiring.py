"""LLM-judge calibration is WIRED and honest (review #11b).

Measuring judge-vs-human agreement needs a model API key (the judge is an LLM).
With no key it must NOT fabricate a number — it reports the honest blocker + the
minimal cost, keeps judge criteria provisional, and is one command from running.
When a key IS present, the runner drives the real judge over the labeled corpus.
"""

from __future__ import annotations

from agenttic.scoring.judge_calibration import (
    JudgeCalibrationBlocked,
    _build,
    corpus_criteria,
    estimate_cost,
    judge_blocker,
    judge_calibration_available,
    load_judge_corpus,
    run_judge_calibration,
)


class TestCorpus:
    def test_corpus_loads_and_is_judge_scored(self):
        recs = load_judge_corpus()
        assert len(recs) >= 12
        crit = corpus_criteria()
        assert {"helpfulness", "tone_professional", "faithfulness_judge"} <= set(crit)

    def test_records_build_into_judge_criteria(self):
        for rec in load_judge_corpus():
            criterion, trace, case = _build(rec)
            assert criterion.scorer == "judge"
            assert criterion.anchors.get("pass") and criterion.anchors.get("fail")
            assert trace.final_output == rec["final_output"]


class TestBlockerWhenNoKey:
    def test_no_key_blocks_without_spending(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert judge_calibration_available() is False
        import pytest
        with pytest.raises(JudgeCalibrationBlocked):
            run_judge_calibration({}, path=None)

    def test_blocker_reports_minimal_cost_and_command(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        blk = judge_blocker({"models": {"judge_strong": "claude-haiku-4-5-20251001"}})
        assert blk["demonstrated"] is False
        assert "ANTHROPIC_API_KEY" in blk["blocker"]
        assert blk["minimal_cost"]["n_records"] >= 12
        assert "calibrate-judge" in blk["one_command"]

    def test_cost_estimate_is_small(self):
        est = estimate_cost({"models": {"judge_strong": "claude-haiku-4-5-20251001"}})
        assert est["est_usd"] is None or est["est_usd"] < 0.5


class TestRunsWithFakeJudgeClient:
    def test_run_with_injected_client_measures_agreement(self):
        # Prove the runner actually drives the judge end-to-end (no real key):
        # inject a fake Anthropic client that scores each record perfectly, so
        # agreement is demonstrably computed from judge output vs human labels.
        from agenttic.scoring.judge_calibration import load_judge_corpus

        by_id = {r["record_id"]: r for r in load_judge_corpus()}

        class _Block:
            type = "text"

            def __init__(self, text):
                self.text = text

        class _Usage:
            input_tokens = 10
            output_tokens = 5

        class _Resp:
            def __init__(self, text):
                self.content = [_Block(text)]
                self.usage = _Usage()

        class _Messages:
            def create(self, **kw):
                # Recover which record this prompt is for (its final_output is
                # unique and embedded in the judge prompt) and echo the human
                # score as the judge verdict — a "perfect" judge.
                prompt = kw["messages"][0]["content"]
                rec = next((r for r in by_id.values()
                            if r["final_output"][:40] in prompt), None)
                score = rec["human_score"] if rec else 1.0
                return _Resp('{"score": %s, "rationale": "ok"}' % score)

        class _FakeClient:
            messages = _Messages()

        cfg = {"models": {"judge_strong": "j-model", "agent_default": "a"},
               "scoring": {}}
        result = run_judge_calibration(cfg, client=_FakeClient(), model="a")
        assert result.n_records >= 12
        # a perfect judge agrees fully with the human labels
        assert result.calibrated_criteria == set(corpus_criteria())
        for cal in result.per_criterion.values():
            assert cal.agreement >= 0.99


class TestPublicSurface:
    def test_public_calibration_includes_judge_block(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
            body = c.get("/api/public/calibration").json()   # no auth
            assert "tool_misuse_safety" in body["calibrated_criteria"]
            jc = body["judge_calibration"]
            # the RECORDED real judge-vs-human run is surfaced as a historical
            # record — but it does NOT promote any criterion out of provisional.
            assert jc["recorded"] is True
            assert jc["demonstrated"] is False
            assert jc["promotes_out_of_provisional"] is False
            assert jc["judge_model"] == "claude-sonnet-4-5-20250929"
            # the criteria it MEASURED are surfaced, but none are promoted
            assert {"helpfulness", "tone_professional", "faithfulness_judge"} == \
                set(jc["recorded_criteria"])
            assert jc["promoted_criteria"] == []
            assert jc["reproduce"]["requires"] == "ANTHROPIC_API_KEY"
