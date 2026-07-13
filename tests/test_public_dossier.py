"""T18.1 — public dossier verification page renders from the dossier JSON alone,
with an otherwise-empty registry (SPEC-2 M7)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agenttic.certification.dossier import assemble
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.certification import (
    Attestation,
    CertificationProfile,
    DomainCoverage,
    TierDecision,
)
from agenttic.server.app import create_app

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, token: testtoken}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
"""


def _seed_one_dossier(reg):
    prof = CertificationProfile(profile_id="cert-agent-safety-v1",
                                required_domains=["tool_use", "cbrn_proxy"],
                                caveats=["cbrn_proxy: NOT ASSESSED"])
    return assemble(
        reg, agent_id="ref-agent", agent_config_hash="h",
        profile=prof,
        tier_decision=TierDecision(tier="B", evidence_refs=["canonical:x"],
                                   caps_applied=["provisional_judge"]),
        coverage=[DomainCoverage(domain="tool_use", status="assessed_seed",
                                 evidence_refs=["suite:std-tool-use-v1@v1"]),
                  DomainCoverage(domain="cbrn_proxy", status="not_assessed")],
        attestation=Attestation(mode="self_attested", tenant="default"))


def test_public_certification_renders_from_dossier_alone(tmp_path):
    reg = Registry(tmp_path / "a.db")  # otherwise-empty registry
    d = _seed_one_dossier(reg)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    with TestClient(create_app(str(cfg), registry=reg)) as c:
        # PUBLIC: no auth header
        r = c.get(f"/certification/{d.dossier_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["verified"] is True
        assert body["tier"] == "B"
        assert body["status"] == "current"
        assert body["attestation"] == "self_attested"
        # NOT ASSESSED domain preserved verbatim in the coverage
        cov = {c["domain"]: c["status"] for c in body["dossier"]["coverage"]}
        assert cov["cbrn_proxy"] == "not_assessed"
        # unknown id → 404
        assert c.get("/certification/nope").status_code == 404
