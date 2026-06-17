"""Signup / login / logout endpoints, cookie sessions, CSRF, and that a logged-in
user drives the same RBAC + tenant scoping as the bearer token."""

import pytest
from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app

# auth.required so the API enforces; allow_signup; viewer signup role so we can
# test the operator gate. cookie_secure omitted -> defaults false (TestClient http).
CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, allow_signup: true, signup_role: %(role)s, session_secret: testsecret}
security: {login_max_attempts: 3, login_lockout_seconds: 900}
"""


def _client(tmp_path, role="admin"):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c", "role": role})
    return TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db")))


VERIFY_CONFIG = CONFIG + "email: {require_verification: true, enabled: false}\n"


def _client_verify(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(VERIFY_CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                                    "c": tmp_path / "c", "role": "admin"})
    return TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db")))


class TestEmailVerificationGate:
    def test_signup_unverified_then_verify_unlocks_login(self, tmp_path):
        from ascore.registry.sqlite_store import EmailTokenRow, Registry
        from sqlmodel import Session, select
        with _client_verify(tmp_path) as c:
            r = c.post("/api/auth/signup",
                       json={"email": "v@x.com", "password": "password123"})
            assert r.status_code == 200 and r.json()["needs_verification"] is True
            assert not c.cookies.get("ascore_session")     # no session yet

            # login is blocked until verified
            r = c.post("/api/auth/login",
                       json={"email": "v@x.com", "password": "password123"})
            assert r.status_code == 403
            assert r.json()["detail"]["needs_verification"] is True

            # grab the token the server issued (console mode -> stored in DB)
            reg = Registry(tmp_path / "a.db")
            with Session(reg.engine) as s:
                token = s.exec(select(EmailTokenRow.token).where(
                    EmailTokenRow.email == "v@x.com")).first()
            r = c.post("/api/auth/verify", json={"token": token})
            assert r.status_code == 200 and r.json()["verified"] is True

            # now login works
            c.post("/api/auth/logout")
            assert c.post("/api/auth/login",
                          json={"email": "v@x.com", "password": "password123"}
                          ).status_code == 200

    def test_resend_always_ok(self, tmp_path):
        with _client_verify(tmp_path) as c:
            c.post("/api/auth/signup", json={"email": "v@x.com", "password": "password123"})
            assert c.post("/api/auth/resend-verification",
                          json={"email": "v@x.com"}).status_code == 200
            # unknown address also returns 200 (no account enumeration)
            assert c.post("/api/auth/resend-verification",
                          json={"email": "nobody@x.com"}).status_code == 200


class TestSignupLogin:
    def test_signup_sets_session_and_me(self, tmp_path):
        with _client(tmp_path) as c:
            r = c.post("/api/auth/signup",
                       json={"email": "a@b.com", "password": "password123"})
            assert r.status_code == 200
            assert r.json()["role"] == "admin"
            assert r.json()["tenant"].startswith("a-")  # own workspace
            assert c.cookies.get("ascore_session")        # cookie set
            me = c.get("/api/me").json()
            assert me["email"] == "a@b.com" and me["auth_method"] == "session"

    def test_login_after_signup(self, tmp_path):
        with _client(tmp_path) as c:
            c.post("/api/auth/signup", json={"email": "a@b.com", "password": "password123"})
            c.post("/api/auth/logout")
            assert c.get("/api/agents").status_code == 401  # session cleared
            r = c.post("/api/auth/login", json={"email": "a@b.com", "password": "password123"})
            assert r.status_code == 200
            assert c.get("/api/agents").status_code == 200  # logged back in

    def test_bad_password_401(self, tmp_path):
        with _client(tmp_path) as c:
            c.post("/api/auth/signup", json={"email": "a@b.com", "password": "password123"})
            c.post("/api/auth/logout")
            assert c.post("/api/auth/login",
                          json={"email": "a@b.com", "password": "wrong"}).status_code == 401

    def test_duplicate_signup_409(self, tmp_path):
        with _client(tmp_path) as c:
            c.post("/api/auth/signup", json={"email": "a@b.com", "password": "password123"})
            assert c.post("/api/auth/signup",
                          json={"email": "a@b.com", "password": "password123"}).status_code == 409

    def test_lockout_after_repeated_failures(self, tmp_path):
        with _client(tmp_path) as c:
            c.post("/api/auth/signup", json={"email": "a@b.com", "password": "password123"})
            c.post("/api/auth/logout")
            for _ in range(3):
                c.post("/api/auth/login", json={"email": "a@b.com", "password": "x"})
            # now locked -> 429 even with the right password
            assert c.post("/api/auth/login",
                          json={"email": "a@b.com", "password": "password123"}).status_code == 429


class TestSessionAuthz:
    def test_viewer_session_blocked_from_writes(self, tmp_path):
        with _client(tmp_path, role="viewer") as c:
            c.post("/api/auth/signup", json={"email": "v@b.com", "password": "password123"})
            assert c.get("/api/agents").status_code == 200            # viewer can read
            csrf = c.cookies.get("ascore_csrf")
            r = c.post("/api/agents/catalog", headers={"X-CSRF-Token": csrf},
                       json={"agent_id": "x", "variant": "reference"})
            assert r.status_code == 403                                # operator-only

    def test_operator_session_can_write_with_csrf(self, tmp_path):
        with _client(tmp_path, role="operator") as c:
            c.post("/api/auth/signup", json={"email": "o@b.com", "password": "password123"})
            csrf = c.cookies.get("ascore_csrf")
            r = c.post("/api/agents/catalog", headers={"X-CSRF-Token": csrf},
                       json={"agent_id": "x", "variant": "reference"})
            assert r.status_code == 200

    def test_csrf_required_for_cookie_mutations(self, tmp_path):
        with _client(tmp_path, role="admin") as c:
            c.post("/api/auth/signup", json={"email": "a@b.com", "password": "password123"})
            # no X-CSRF-Token header -> 403 even though authenticated
            r = c.post("/api/agents/catalog",
                       json={"agent_id": "x", "variant": "reference"})
            assert r.status_code == 403

    def test_session_is_tenant_scoped(self, tmp_path):
        # two signups -> two tenants -> isolated catalogs
        with _client(tmp_path, role="admin") as c1:
            c1.post("/api/auth/signup", json={"email": "a@b.com", "password": "password123"})
            csrf = c1.cookies.get("ascore_csrf")
            c1.post("/api/agents/catalog", headers={"X-CSRF-Token": csrf},
                    json={"agent_id": "a-bot", "variant": "reference"})
            mine = c1.get("/api/agents/catalog").json()["agents"]
            assert [a["agent_id"] for a in mine] == ["a-bot"]
        with _client(tmp_path, role="admin") as c2:
            c2.post("/api/auth/signup", json={"email": "z@b.com", "password": "password123"})
            assert c2.get("/api/agents/catalog").json()["agents"] == []  # different tenant


def test_bearer_token_still_works_alongside_sessions(tmp_path):
    # configure a bearer token too; it must keep working (CI/API clients)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l}\n"
        "harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path / 'a.db'}, review_dir: {tmp_path / 'r'}, calibration_dir: {tmp_path / 'c'}}}\n"
        'auth: {required: true, token: "btok", allow_signup: true}\n')
    with TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db"))) as c:
        assert c.get("/api/agents").status_code == 401  # no creds
        assert c.get("/api/agents", headers={"Authorization": "Bearer btok"}).status_code == 200
        # bearer needs no CSRF (header auth)
        assert c.post("/api/agents/catalog", headers={"Authorization": "Bearer btok"},
                      json={"agent_id": "x", "variant": "reference"}).status_code == 200
