"""T27.4 — public verify/card pages render 'enforced under policy <hash>' from
the compiled policy alone (SPEC-2 M13)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agenttic.certification.dossier import assemble
from agenttic.config import load_config
from agenttic.enforce.compiler import recompile_for_agent
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)
from agenttic.server.app import create_app

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: false, token: ""}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
"""


def test_verify_page_shows_policy_hash_and_posture(tmp_path):
    reg = Registry(tmp_path / "a.db")
    prof = CertificationProfile(profile_id="p", required_domains=["tool_use"])
    d = assemble(reg, agent_id="ref", agent_config_hash="h", profile=prof,
                 tier_decision=TierDecision(tier="B", evidence_refs=["e"],
                                            caps_applied=["provisional_judge"]),
                 coverage=[], attestation=Attestation(mode="self_attested",
                                                      tenant="default"))
    cfg = load_config("config.yaml")
    policy = recompile_for_agent(reg, cfg, "ref")

    cfgf = tmp_path / "config.yaml"
    cfgf.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                              "c": tmp_path / "c"})
    with TestClient(create_app(str(cfgf), registry=reg)) as c:
        body = c.get(f"/certification/{d.dossier_id}").json()
        enf = body["enforcement"]
        assert enf is not None
        assert enf["policy_hash"] == policy.content_hash
        # Tier B posture: write approvals + elevated sampling, serving
        assert enf["serve"] == "allow"
        assert enf["approvals"] == "write"
        assert enf["lane3_sampling"] > 0
        # rendered from the policy alone (compiled_from names the dossier)
        assert any(r.startswith("dossier:") for r in enf["compiled_from"])
