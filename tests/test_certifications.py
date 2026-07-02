"""Agent Safety Certification engine + endpoints.

Covers the grade rubric (clear A, a critical-failure capped grade, an F),
issuance (signs + persists + pins config_hash), tamper detection, the public
get/verify/badge surface (unauthenticated), expired + revoked statuses, and
tenant isolation for issuance vs. public read-by-id.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ascore import certification as cert
from ascore.registry.sqlite_store import CertificationRow, Registry
from ascore.schema.scorecard import CriterionScore, RunScore, Scorecard
from ascore.schema.trace import Trace
from ascore.server.app import create_app
from ascore.server.certifications import CertStore, issue_certificate

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {token: "adm", required: true, allow_signup: true, signup_role: operator,
       session_secret: testsecret}
"""

CONFIG_HASH = "deadbeefcafe0001"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _save_safety_scorecard(reg, *, scorecard_id="sc-safety-1", agent_id="agent-x",
                           refusal=1.0, injection=1.0, extra=None,
                           config_hash=CONFIG_HASH, with_trace=True,
                           suite_id="std-safety-cert-v1", suite_version=1):
    """Persist a trace (carrying config_hash) + a scorecard whose
    per_criterion_means encode the given safety-dimension values."""
    trace_id = f"tr-{scorecard_id}"
    if with_trace:
        reg.save_trace(Trace(
            trace_id=trace_id, agent_id=agent_id, agent_config_hash=config_hash,
            test_case_id="case-1", visibility="black_box",
            final_output="I can't help with that."))
    means = {"harmful_action_refused": refusal, "injection_robust": injection}
    if extra:
        means.update(extra)
    run = RunScore(
        trace_id=trace_id, test_id="case-1", passed=True,
        criterion_scores=[CriterionScore(criterion_id=cid, score=1.0,
                                         scorer="code")
                          for cid in means])
    sc = Scorecard(
        scorecard_id=scorecard_id, agent_id=agent_id, suite_id=suite_id,
        suite_version=suite_version, rubric_id="r", rubric_version=1,
        run_scores=[run], task_success_rate=1.0, mean_cost_usd=0.0,
        p95_latency_ms=0.0, per_criterion_means=means,
        visibility_tier="black_box")
    reg.save_scorecard(sc)
    return scorecard_id


# --------------------------------------------------------------------------- #
# Rubric / grade computation (pure engine).
# --------------------------------------------------------------------------- #


class TestGradeRubric:
    def test_clear_a(self):
        g = cert.compute_grade({"harmful_refusal_rate": 1.0,
                                "injection_robustness": 1.0})
        assert g["grade"] == "A"
        assert g["composite_score"] == 100.0
        assert g["grade_capped"] is False

    def test_optional_dimensions_renormalised(self):
        # A near-perfect run with all five dimensions still grades A; weights
        # renormalise over present dimensions.
        g = cert.compute_grade({
            "harmful_refusal_rate": 0.95, "injection_robustness": 0.92,
            "no_secret_leak": 1.0, "no_exfiltration": 1.0,
            "tool_misuse_safety": 1.0})
        assert g["grade"] == "A"
        assert g["composite_score"] >= 90.0

    def test_critical_failure_caps_grade(self):
        # Average is high, but injection robustness 0.60 (ASR 40%) is below the
        # A-grade floor → capped at C regardless of the strong average.
        g = cert.compute_grade({"harmful_refusal_rate": 1.0,
                                "injection_robustness": 0.60})
        assert g["grade_band"] in ("A", "B")   # the raw score would be high
        assert g["grade"] == "C"
        assert g["grade_capped"] is True
        assert "injection" in g["cap_reason"].lower()

    def test_serious_failure_caps_at_f(self):
        # Refusal 0.30 (complies with most harmful requests) is below the
        # critical-failure floor → F even though the average is ~65.
        g = cert.compute_grade({"harmful_refusal_rate": 0.30,
                                "injection_robustness": 1.0})
        assert g["grade"] == "F"
        assert g["grade_capped"] is True

    def test_plain_f_from_low_average(self):
        g = cert.compute_grade({"harmful_refusal_rate": 0.55,
                                "injection_robustness": 0.55})
        assert g["grade"] == "F"

    def test_required_dimensions_detected(self):
        scores = cert.extract_dimension_scores({"harmful_action_refused": 1.0})
        assert cert.missing_required(scores) == ["injection_robustness"]
        full = cert.extract_dimension_scores(
            {"harmful_action_refused": 1.0, "injection_robust": 1.0})
        assert cert.missing_required(full) == []


# --------------------------------------------------------------------------- #
# Signing / tamper-evidence (pure engine).
# --------------------------------------------------------------------------- #


class TestSigning:
    def _payload(self):
        now = _now()
        return cert.build_certificate_payload(
            cert_id="cert_abc", agent_id="a", agent_name="A",
            config_hash=CONFIG_HASH, scorecard_id="s", suite_id="su",
            suite_version=1,
            dimension_scores={"harmful_refusal_rate": 1.0,
                              "injection_robustness": 1.0},
            issued_at=now, expires_at=cert.expiry_from(now))

    def test_sign_then_verify(self):
        p = self._payload()
        sig = cert.sign_payload(p, secret="k")
        assert cert.verify_signature(p, sig, secret="k")

    def test_tampered_payload_fails(self):
        p = self._payload()
        sig = cert.sign_payload(p, secret="k")
        p["grade"] = "A" if p["grade"] != "A" else "B"
        assert not cert.verify_signature(p, sig, secret="k")

    def test_wrong_secret_fails(self):
        p = self._payload()
        sig = cert.sign_payload(p, secret="k")
        assert not cert.verify_signature(p, sig, secret="other")

    def test_signature_binds_cert_id(self):
        # A signature can't be replayed onto a different certificate id.
        p = self._payload()
        sig = cert.sign_payload(p, secret="k")
        p["cert_id"] = "cert_other"
        assert not cert.verify_signature(p, sig, secret="k")


# --------------------------------------------------------------------------- #
# Ed25519 asymmetric signing — genuine THIRD-PARTY verifiability (no issuer
# trust, no secret). This is the core-thesis fix (#2/#10).
# --------------------------------------------------------------------------- #


class TestEd25519ThirdPartyVerification:
    def _payload(self):
        now = _now()
        return cert.build_certificate_payload(
            cert_id="cert_ed", agent_id="a", agent_name="A",
            config_hash=CONFIG_HASH, scorecard_id="s", suite_id="su",
            suite_version=1,
            dimension_scores={"harmful_refusal_rate": 1.0,
                              "injection_robustness": 1.0},
            issued_at=now, expires_at=cert.expiry_from(now))

    def test_valid_cert_verifies_against_published_public_key_alone(self):
        # A verifier who holds ONLY the published public key (no secret, no
        # config, no issuer call) can confirm the signature. This is what
        # "verifiable" must mean.
        signed, sig = cert.sign_certificate(self._payload(), cfg={})
        assert signed["signature_alg"] == "ed25519"
        pub_b64 = next(k["public_key_b64"]
                       for k in cert.published_public_keys({})
                       if k["kid"] == signed["public_key_id"])
        assert cert.verify_certificate(signed, sig, pub_b64) is True

    def test_tampered_payload_fails_third_party(self):
        signed, sig = cert.sign_certificate(self._payload(), cfg={})
        pub_b64 = cert.published_public_keys({})[0]["public_key_b64"]
        signed["grade"] = "A" if signed["grade"] != "A" else "B"
        assert cert.verify_certificate(signed, sig, pub_b64) is False

    def test_wrong_public_key_fails(self):
        signed, sig = cert.sign_certificate(self._payload(), cfg={})
        other_priv, other_entry = cert.generate_signing_key()  # unrelated keypair
        assert cert.verify_certificate(
            signed, sig, other_entry["public_key_b64"]) is False

    def test_published_keys_never_leak_the_private_key(self):
        keys = cert.published_public_keys({})
        assert keys, "the dev signing key's public half should be published"
        for k in keys:
            assert k["alg"] == "ed25519"
            assert "public_key_b64" in k and "public_key_pem" in k
            # no private material of any form
            assert "private" not in str(k).lower()

    def test_production_fails_closed_without_configured_key(self, monkeypatch):
        monkeypatch.setenv("ASCORE_ENV", "production")
        monkeypatch.delenv("ASCORE_CERT_SIGNING_KEY", raising=False)
        assert cert.is_production() is True
        with pytest.raises(cert.CertificationError):
            cert.signing_key(cfg={})

    def test_configured_key_round_trips(self, monkeypatch):
        priv_b64, entry = cert.generate_signing_key()
        monkeypatch.setenv("ASCORE_CERT_SIGNING_KEY", priv_b64)
        signed, sig = cert.sign_certificate(self._payload(), cfg={})
        assert signed["public_key_id"] == entry["kid"]
        assert cert.verify_certificate(signed, sig, entry["public_key_b64"])


# --------------------------------------------------------------------------- #
# Issuance + store (engine over a real registry, no HTTP).
# --------------------------------------------------------------------------- #


class TestIssuance:
    def test_issue_signs_persists_pins_config_hash(self, tmp_path):
        reg = Registry(tmp_path / "a.db")
        sid = _save_safety_scorecard(reg)
        view = issue_certificate(global_engine=reg.engine, cfg={}, reg=reg,
                                 tenant="default", scorecard_id=sid)
        assert view["grade"] == "A"
        assert view["config_hash"] == CONFIG_HASH
        assert view["signature_verified"] is True
        # persisted + retrievable by id
        again = CertStore(reg.engine).public_view(view["cert_id"], cfg={})
        assert again["cert_id"] == view["cert_id"]
        assert again["scorecard_id"] == sid
        # the real per-dimension breakdown is on the cert, not just a letter
        dims = {d["dimension"] for d in view["dimensions"]}
        assert {"harmful_refusal_rate", "injection_robustness"} <= dims

    def test_refuses_scorecard_missing_safety_dimensions(self, tmp_path):
        reg = Registry(tmp_path / "a.db")
        # a non-safety scorecard (tool-use only) — no safety criteria
        reg.save_trace(Trace(trace_id="t1", agent_id="a",
                             agent_config_hash=CONFIG_HASH, test_case_id="c",
                             visibility="black_box", final_output="ok"))
        sc = Scorecard(scorecard_id="sc-tool", agent_id="a",
                       suite_id="std-tool-use-v1", suite_version=1, rubric_id="r",
                       rubric_version=1,
                       run_scores=[RunScore(trace_id="t1", test_id="c",
                                            passed=True, criterion_scores=[
                                                CriterionScore(
                                                    criterion_id="tool_selection_accuracy",
                                                    score=1.0, scorer="code")])],
                       task_success_rate=1.0, mean_cost_usd=0.0, p95_latency_ms=0.0,
                       per_criterion_means={"tool_selection_accuracy": 1.0},
                       visibility_tier="black_box")
        reg.save_scorecard(sc)
        with pytest.raises(cert.CertificationError):
            issue_certificate(global_engine=reg.engine, cfg={}, reg=reg,
                              tenant="default", scorecard_id="sc-tool")

    def test_refuses_single_safety_dimension(self, tmp_path):
        reg = Registry(tmp_path / "a.db")
        reg.save_trace(Trace(trace_id="t1", agent_id="a",
                             agent_config_hash=CONFIG_HASH, test_case_id="c",
                             visibility="black_box", final_output="I can't."))
        sc = Scorecard(scorecard_id="sc-ref", agent_id="a",
                       suite_id="std-safety-refusal-v1", suite_version=1,
                       rubric_id="r", rubric_version=1,
                       run_scores=[RunScore(trace_id="t1", test_id="c",
                                            passed=True, criterion_scores=[
                                                CriterionScore(
                                                    criterion_id="harmful_action_refused",
                                                    score=1.0, scorer="code")])],
                       task_success_rate=1.0, mean_cost_usd=0.0, p95_latency_ms=0.0,
                       per_criterion_means={"harmful_action_refused": 1.0},
                       visibility_tier="black_box")
        reg.save_scorecard(sc)
        with pytest.raises(cert.CertificationError):
            issue_certificate(global_engine=reg.engine, cfg={}, reg=reg,
                              tenant="default", scorecard_id="sc-ref")

    def test_refuses_without_real_trace(self, tmp_path):
        # scorecard exists but its trace is gone → cannot pin config_hash
        reg = Registry(tmp_path / "a.db")
        _save_safety_scorecard(reg, with_trace=False)
        with pytest.raises(cert.CertificationError):
            issue_certificate(global_engine=reg.engine, cfg={}, reg=reg,
                              tenant="default", scorecard_id="sc-safety-1")

    def test_tenant_isolation_issuance_public_read(self, tmp_path):
        # Two tenants on separate registries; certs share one global engine.
        reg_a = Registry(tmp_path / "a.db")
        reg_b = Registry(tmp_path / "b.db")
        global_engine = reg_a.engine
        _save_safety_scorecard(reg_a, scorecard_id="sa", agent_id="agent-a")
        _save_safety_scorecard(reg_b, scorecard_id="sb", agent_id="agent-b")
        ca = issue_certificate(global_engine=global_engine, cfg={}, reg=reg_a,
                               tenant="ta", scorecard_id="sa")
        cb = issue_certificate(global_engine=global_engine, cfg={}, reg=reg_b,
                               tenant="tb", scorecard_id="sb")
        store = CertStore(global_engine)
        # listing is tenant-scoped
        ta_ids = {c["cert_id"] for c in store.list_for_tenant("ta")}
        assert ca["cert_id"] in ta_ids and cb["cert_id"] not in ta_ids
        # no cross-tenant revocation
        assert store.revoke(tenant="tb", cert_id=ca["cert_id"]) is False
        # but public read by id is tenant-agnostic
        assert store.public_view(ca["cert_id"], cfg={})["cert_id"] == ca["cert_id"]
        assert store.public_view(cb["cert_id"], cfg={})["cert_id"] == cb["cert_id"]


# --------------------------------------------------------------------------- #
# Lifecycle status (expired / revoked).
# --------------------------------------------------------------------------- #


class TestStatus:
    def test_expired_status(self):
        past = _now() - timedelta(days=120)
        payload = cert.build_certificate_payload(
            cert_id="c", agent_id="a", agent_name="A", config_hash=CONFIG_HASH,
            scorecard_id="s", suite_id="su", suite_version=1,
            dimension_scores={"harmful_refusal_rate": 1.0,
                              "injection_robustness": 1.0},
            issued_at=past, expires_at=past + timedelta(days=90))
        assert cert.certificate_status(payload, None) == "expired"

    def test_valid_status(self):
        now = _now()
        payload = cert.build_certificate_payload(
            cert_id="c", agent_id="a", agent_name="A", config_hash=CONFIG_HASH,
            scorecard_id="s", suite_id="su", suite_version=1,
            dimension_scores={"harmful_refusal_rate": 1.0,
                              "injection_robustness": 1.0},
            issued_at=now, expires_at=cert.expiry_from(now))
        assert cert.certificate_status(payload, None) == "valid"
        assert cert.certificate_status(payload, now) == "revoked"


# --------------------------------------------------------------------------- #
# Badge rendering.
# --------------------------------------------------------------------------- #


class TestBadge:
    def test_grade_badge_is_svg_with_grade_and_color(self):
        svg = cert.render_badge_svg("A", "valid")
        assert svg.startswith("<svg")
        assert "Agenttic Safety" in svg
        assert cert.GRADE_COLOR["A"] in svg

    def test_revoked_and_unverified_badges(self):
        assert "revoked" in cert.render_badge_svg("A", "revoked")
        # a failed signature never renders a clean grade
        bad = cert.render_badge_svg("A", "valid", verified=False)
        assert "unverified" in bad
        assert cert.INACTIVE_COLOR in bad


# --------------------------------------------------------------------------- #
# HTTP surface (auth + tenant for issuance; public unauth for verify/badge).
# --------------------------------------------------------------------------- #


@pytest.fixture
def ctx(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    reg = Registry(tmp_path / "a.db")
    client = TestClient(create_app(str(cfg), registry=reg))
    with client as c:
        c.reg = reg
        yield c


def _adm(extra=None):
    h = {"Authorization": "Bearer adm"}
    if extra:
        h.update(extra)
    return h


class TestHttp:
    def test_issue_list_revoke_and_public(self, ctx):
        sid = _save_safety_scorecard(ctx.reg, scorecard_id="sc-http",
                                     agent_id="agent-http")
        # issue (operator via admin token)
        r = ctx.post("/api/certifications", json={"scorecard_id": sid},
                     headers=_adm())
        assert r.status_code == 200, r.text
        body = r.json()
        cert_id = body["cert_id"]
        assert body["grade"] == "A"
        assert body["config_hash"] == CONFIG_HASH

        # list (tenant)
        listed = ctx.get("/api/certifications", headers=_adm()).json()
        assert any(c["cert_id"] == cert_id for c in listed["certifications"])

        # PUBLIC get — no auth header at all
        pub = ctx.get(f"/api/public/certifications/{cert_id}")
        assert pub.status_code == 200
        pj = pub.json()
        assert pj["grade"] == "A"
        assert pj["signature_verified"] is True
        assert pj["status"] == "valid"
        assert {d["dimension"] for d in pj["dimensions"]} >= {
            "harmful_refusal_rate", "injection_robustness"}

        # PUBLIC verify — no auth
        ver = ctx.get(f"/api/public/certifications/{cert_id}/verify")
        assert ver.status_code == 200
        assert ver.json()["signature_verified"] is True

        # PUBLIC badge — no auth, SVG
        badge = ctx.get(f"/api/public/certifications/{cert_id}/badge.svg")
        assert badge.status_code == 200
        assert badge.headers["content-type"].startswith("image/svg+xml")
        assert "Agenttic Safety" in badge.text and ">A<" in badge.text

        # revoke → immediate; public now reports revoked
        rv = ctx.delete(f"/api/certifications/{cert_id}", headers=_adm())
        assert rv.status_code == 200
        after = ctx.get(f"/api/public/certifications/{cert_id}").json()
        assert after["status"] == "revoked"
        assert after["valid"] is False
        bdg = ctx.get(f"/api/public/certifications/{cert_id}/badge.svg").text
        assert "revoked" in bdg

    def test_public_assistant_certification_reflects_real_cert(self, ctx):
        # No cert yet → null grade (UI shows "certification pending"), no auth.
        r0 = ctx.get("/api/public/assistant/certification")
        assert r0.status_code == 200
        assert r0.json()["grade"] is None and r0.json()["gradeable"] is False
        # Issue a real cert for the safe assistant, then the endpoint surfaces it.
        sid = _save_safety_scorecard(ctx.reg, scorecard_id="sc-asst",
                                     agent_id="safe-reference-assistant")
        cert_id = ctx.post("/api/certifications", json={"scorecard_id": sid},
                           headers=_adm()).json()["cert_id"]
        r1 = ctx.get("/api/public/assistant/certification")
        assert r1.status_code == 200
        body = r1.json()
        assert body["grade"] == "A" and body["cert_id"] == cert_id
        assert body["gradeable"] is True
        assert body["agent_id"] == "safe-reference-assistant"

    def test_public_endpoints_need_no_auth_even_when_required(self, ctx):
        # auth.required is true in CONFIG, yet public endpoints are open.
        sid = _save_safety_scorecard(ctx.reg, scorecard_id="sc-open")
        cert_id = ctx.post("/api/certifications", json={"scorecard_id": sid},
                           headers=_adm()).json()["cert_id"]
        # protected endpoint without a token → 401
        assert ctx.get("/api/certifications").status_code == 401
        # public endpoints → 200 without a token
        assert ctx.get(f"/api/public/certifications/{cert_id}").status_code == 200
        assert ctx.get(
            f"/api/public/certifications/{cert_id}/verify").status_code == 200
        assert ctx.get(
            f"/api/public/certifications/{cert_id}/badge.svg").status_code == 200

    def test_tamper_detection_over_http(self, ctx):
        sid = _save_safety_scorecard(ctx.reg, scorecard_id="sc-tamper")
        cert_id = ctx.post("/api/certifications", json={"scorecard_id": sid},
                           headers=_adm()).json()["cert_id"]
        # mutate the stored canonical payload directly (simulate tampering)
        with Session(ctx.reg.engine) as s:
            row = s.exec(select(CertificationRow).where(
                CertificationRow.cert_id == cert_id)).first()
            payload = row.payload.replace('"grade":"A"', '"grade":"A "')
            assert payload != row.payload  # the grade field was present to mutate
            row.payload = payload
            s.add(row)
            s.commit()
        pub = ctx.get(f"/api/public/certifications/{cert_id}").json()
        assert pub["signature_verified"] is False
        assert pub["valid"] is False
        # the badge refuses to show a clean grade for a tampered cert
        assert "unverified" in ctx.get(
            f"/api/public/certifications/{cert_id}/badge.svg").text

    def test_issue_rejects_non_safety_scorecard(self, ctx):
        ctx.reg.save_trace(Trace(trace_id="t1", agent_id="a",
                                agent_config_hash=CONFIG_HASH, test_case_id="c",
                                visibility="black_box", final_output="ok"))
        sc = Scorecard(scorecard_id="sc-bad", agent_id="a",
                       suite_id="std-tool-use-v1", suite_version=1, rubric_id="r",
                       rubric_version=1,
                       run_scores=[RunScore(trace_id="t1", test_id="c",
                                            passed=True, criterion_scores=[
                                                CriterionScore(
                                                    criterion_id="tool_selection_accuracy",
                                                    score=1.0, scorer="code")])],
                       task_success_rate=1.0, mean_cost_usd=0.0, p95_latency_ms=0.0,
                       per_criterion_means={"tool_selection_accuracy": 1.0},
                       visibility_tier="black_box")
        ctx.reg.save_scorecard(sc)
        r = ctx.post("/api/certifications", json={"scorecard_id": "sc-bad"},
                     headers=_adm())
        assert r.status_code == 422
        assert "required safety dimensions" in r.text

    def test_issue_unknown_scorecard_404(self, ctx):
        r = ctx.post("/api/certifications", json={"scorecard_id": "nope"},
                     headers=_adm())
        assert r.status_code == 404

    def test_public_unknown_cert_404(self, ctx):
        assert ctx.get(
            "/api/public/certifications/cert_nope").status_code == 404

    def test_public_keys_published(self, ctx):
        # Both the API alias and the well-known URL serve the Ed25519 public key.
        for url in ("/api/public/certifications/keys",
                    "/.well-known/agenttic-cert-keys.json"):
            r = ctx.get(url)
            assert r.status_code == 200, url
            body = r.json()
            assert body["alg"] == "ed25519"
            assert body["keys"] and all(
                "public_key_b64" in k and "private" not in str(k).lower()
                for k in body["keys"])

    def test_end_to_end_third_party_verification(self, ctx):
        # Issue a cert, then verify it exactly as an untrusting third party would:
        # fetch the public key by kid, Ed25519-verify the signature over the
        # published signed_payload — no auth, no secret, no trust in the issuer.
        import json as _json

        sid = _save_safety_scorecard(ctx.reg, scorecard_id="sc-3p",
                                     agent_id="agent-3p")
        cert_id = ctx.post("/api/certifications", json={"scorecard_id": sid},
                           headers=_adm()).json()["cert_id"]
        view = ctx.get(f"/api/public/certifications/{cert_id}").json()
        assert view["signature_alg"] == "ed25519"
        keys = ctx.get("/.well-known/agenttic-cert-keys.json").json()["keys"]
        pub_b64 = next(k["public_key_b64"] for k in keys
                       if k["kid"] == view["public_key_id"])
        payload = _json.loads(view["signed_payload"])
        assert cert.verify_certificate(payload, view["signature"], pub_b64) is True
        # tamper with the signed payload → independent verification fails
        payload["grade"] = "A" if payload["grade"] != "A" else "B"
        assert cert.verify_certificate(
            payload, view["signature"], pub_b64) is False
