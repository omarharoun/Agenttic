"""Progressive enforcement ramp semantics (SPEC-7 Step 39, T39.4).

Pins the acceptance: shadow logs a would-be block without blocking; enforce_all
blocks the *same* call under the *same* policy; step-down to observe is always
permitted; and a mode change never loosens the compiled policy.
"""
from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from ascore.enforce import ramp
from ascore.enforce.feedback import checker_eval_cases, hardening_candidates
from ascore.enforce.gateway import EnforcementGateway, compute_policy_hash
from ascore.registry.sqlite_store import Registry
from ascore.schema.enforcement import EnforcementPolicy, Rule
from ascore.server.app import create_app

# read-class "fetch" and write-class "shell.exec", both denied by policy.
CFG = {"enforcement": {"action_classes": {"read": ["fetch"], "write": ["shell.exec"]}}}


def _setup(tmp):
    reg = Registry(db_path=f"{tmp}/t.db")
    p = EnforcementPolicy(policy_id="p1", agent_id="a", rules=[
        Rule(rule_id="r1", lane="lane1", action="deny",
             matcher={"tool": "shell.exec"}, origin="tier_posture:C"),
        Rule(rule_id="r2", lane="lane1", action="deny",
             matcher={"tool": "fetch"}, origin="tier_posture:C")])
    p.content_hash = compute_policy_hash(p)
    reg.save_policy(p)
    gw = EnforcementGateway(reg, CFG)
    sess = gw.start_session("a")
    return reg, gw, sess, p


# --- unit: effective_action matrix -----------------------------------------

def test_default_mode_is_observe():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        assert ramp.current_mode(reg, "a") == "observe"


def test_effective_action_matrix():
    assert ramp.effective_action("deny", "write", "shadow")["blocked"] is False
    assert ramp.effective_action("deny", "read", "enforce_reads")["blocked"] is True
    assert ramp.effective_action("deny", "write", "enforce_reads")["blocked"] is False
    assert ramp.effective_action("deny", "write", "enforce_reads")["would_block"] is True
    assert ramp.effective_action("deny", "write", "enforce_all")["blocked"] is True
    assert ramp.effective_action("allow", "write", "enforce_all")["blocked"] is False


# --- shadow non-blocking, enforce_all blocks (same policy, different mode) --

def test_shadow_logs_would_be_block_without_blocking():
    with tempfile.TemporaryDirectory() as tmp:
        reg, gw, sess, _ = _setup(tmp)
        ramp.set_mode(reg, "a", "shadow", "alice@x")
        out = ramp.ramped_evaluate(gw, reg, "a", sess.session_id, "shell.exec", {})
        # policy says deny, but shadow lets it through and records the would-be
        assert out["decision"].action == "deny"
        assert out["allowed"] is True
        assert out["blocked"] is False
        assert out["would_block"] is True
        rep = ramp.shadow_report(reg, "a")
        assert rep["would_be_blocks"] == 1
        assert rep["by_tool"]["shell.exec"] == 1


def test_enforce_all_blocks_the_same_call():
    with tempfile.TemporaryDirectory() as tmp:
        reg, gw, sess, _ = _setup(tmp)
        ramp.set_mode(reg, "a", "enforce_all", "alice@x")
        out = ramp.ramped_evaluate(gw, reg, "a", sess.session_id, "shell.exec", {})
        assert out["decision"].action == "deny"
        assert out["allowed"] is False
        assert out["blocked"] is True
        # a call that actually blocked is not a "would-be" shadow entry
        assert ramp.shadow_report(reg, "a")["would_be_blocks"] == 0


def test_enforce_reads_blocks_reads_shadows_writes():
    with tempfile.TemporaryDirectory() as tmp:
        reg, gw, sess, _ = _setup(tmp)
        ramp.set_mode(reg, "a", "enforce_reads", "alice@x")
        read_out = ramp.ramped_evaluate(gw, reg, "a", sess.session_id, "fetch", {})
        write_out = ramp.ramped_evaluate(gw, reg, "a", sess.session_id, "shell.exec", {})
        assert read_out["blocked"] is True          # read-class enforced
        assert write_out["blocked"] is False         # write-class shadowed
        assert write_out["would_block"] is True
        assert ramp.shadow_report(reg, "a")["would_be_blocks"] == 1


# --- step-down always works ------------------------------------------------

def test_step_down_to_observe_always_works():
    with tempfile.TemporaryDirectory() as tmp:
        reg, _, _, _ = _setup(tmp)
        ramp.set_mode(reg, "a", "enforce_all", "alice@x")
        res = ramp.set_mode(reg, "a", "observe", "bob@x")   # safety valve
        assert res["direction"] == "step_down"
        assert ramp.current_mode(reg, "a") == "observe"
        # and after reverting, the same call no longer blocks
        gw = EnforcementGateway(reg, CFG)
        sess = gw.start_session("a")
        out = ramp.ramped_evaluate(gw, reg, "a", sess.session_id, "shell.exec", {})
        assert out["blocked"] is False


def test_skip_straight_to_enforce_all_is_allowed_by_explicit_action():
    with tempfile.TemporaryDirectory() as tmp:
        reg, _, _, _ = _setup(tmp)
        res = ramp.set_mode(reg, "a", "enforce_all", "alice@x")
        assert res["from"] == "observe" and res["to"] == "enforce_all"
        assert res["direction"] == "advance"


# --- append-only, actor-stamped --------------------------------------------

def test_mode_changes_are_append_only_with_actor():
    with tempfile.TemporaryDirectory() as tmp:
        reg, _, _, _ = _setup(tmp)
        ramp.set_mode(reg, "a", "shadow", "alice@x")
        ramp.set_mode(reg, "a", "enforce_all", "bob@x")
        ramp.set_mode(reg, "a", "observe", "carol@x")
        hist = ramp.mode_history(reg, "a")
        assert [h["to"] for h in hist] == ["shadow", "enforce_all", "observe"]
        assert [h["actor"] for h in hist] == ["alice@x", "bob@x", "carol@x"]


def test_mode_change_requires_actor_and_valid_mode():
    with tempfile.TemporaryDirectory() as tmp:
        reg, _, _, _ = _setup(tmp)
        with pytest.raises(ramp.RampError):
            ramp.set_mode(reg, "a", "shadow", "")
        with pytest.raises(ramp.RampError):
            ramp.set_mode(reg, "a", "turbo", "alice@x")


# --- mode change never loosens the compiled policy -------------------------

def test_mode_change_never_touches_the_compiled_policy():
    with tempfile.TemporaryDirectory() as tmp:
        reg, _, _, policy = _setup(tmp)
        before = reg.latest_policy("a").content_hash
        assert before == policy.content_hash
        for mode in ("shadow", "enforce_reads", "enforce_all", "observe"):
            ramp.set_mode(reg, "a", mode, "alice@x")
            after = reg.latest_policy("a").content_hash
            assert after == before, f"mode {mode} altered the policy hash"
            ramp.assert_policy_unchanged(reg, "a", before)   # must not raise


# --- FP candidates feed the SPEC-4 hardening loop --------------------------

def test_shadow_false_positive_feeds_hardening_loop():
    with tempfile.TemporaryDirectory() as tmp:
        reg, gw, sess, _ = _setup(tmp)
        ramp.set_mode(reg, "a", "shadow", "alice@x")
        ramp.ramped_evaluate(gw, reg, "a", sess.session_id, "shell.exec", {})
        sev = ramp.shadow_report(reg, "a")["shadow_events"][0]
        ramp.mark_shadow_false_positive(reg, "a", sev["event_id"], "reviewer@x", "benign")
        # flows into BOTH hardening candidates and checker-eval cases
        assert any((e.get("detail") or {}).get("shadow_fp")
                   for e in hardening_candidates(reg, agent_id="a"))
        assert len(checker_eval_cases(reg, "a")) >= 1
        assert ramp.shadow_report(reg, "a")["fp_candidate_count"] == 1


# --- API surface -----------------------------------------------------------

def _app(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l}\n"
        "harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path / 'a.db'}, review_dir: {tmp_path / 'r'}, "
        f"calibration_dir: {tmp_path / 'c'}}}\n"
        "auth: {required: true, token: t}\n"
        "enforcement: {action_classes: {read: [fetch], write: [shell.exec]}}\n"
        "security: {login_max_attempts: 5, login_lockout_seconds: 900}\n")
    reg = Registry(db_path=str(tmp_path / "a.db"))
    p = EnforcementPolicy(policy_id="p1", agent_id="a", rules=[
        Rule(rule_id="r1", lane="lane1", action="deny",
             matcher={"tool": "shell.exec"}, origin="tier_posture:C")])
    p.content_hash = compute_policy_hash(p)
    reg.save_policy(p)
    return create_app(str(cfg), registry=reg), reg


def test_api_mode_and_shadow_report(tmp_path):
    app, reg = _app(tmp_path)
    with TestClient(app) as c:
        h = {"Authorization": "Bearer t"}
        assert c.get("/api/enforce/mode?agent_id=a", headers=h).json()["mode"] == "observe"
        r = c.post("/api/enforce/mode", headers=h,
                   json={"agent_id": "a", "mode": "shadow"})
        assert r.json()["direction"] == "advance"
        # step-down via API always works
        r2 = c.post("/api/enforce/mode", headers=h,
                    json={"agent_id": "a", "mode": "observe"})
        assert r2.json()["direction"] == "step_down"
        # invalid mode → 400, not silent
        assert c.post("/api/enforce/mode", headers=h,
                      json={"agent_id": "a", "mode": "x"}).status_code == 400
        sr = c.get("/api/enforce/shadow-report?agent_id=a", headers=h)
        assert sr.status_code == 200 and sr.json()["agent_id"] == "a"
