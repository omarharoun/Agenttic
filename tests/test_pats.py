"""Personal API tokens (PATs): create→use authenticates as the owning user's
tenant + role; revocation is immediate; the plaintext is shown only once; and a
PAT is scoped to its owner's tenant (can't read another tenant's data)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app
from ascore.server.pats import PatStore
from ascore.server.users import UserStore

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {token: "adm", required: true, allow_signup: true, signup_role: operator,
       session_secret: testsecret}
"""


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


def _signup(c, email="dev@x.com", pw="password123"):
    r = c.post("/api/auth/signup", json={"email": email, "password": pw})
    assert r.status_code == 200
    return c.cookies.get("ascore_csrf")


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestPatLifecycle:
    def test_create_use_authenticates_as_user(self, ctx):
        csrf = _signup(ctx)
        # the user's identity via their login session (their own tenant + role)
        session_me = ctx.get("/api/me").json()
        r = ctx.post("/api/settings/tokens", json={"name": "ci"},
                     headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        body = r.json()
        token = body["token"]
        assert token.startswith("agt_") and body["masked"].startswith("agt_…")

        # Use the PAT (bearer) to authenticate as that user — same tenant+role.
        me = ctx.get("/api/me", headers=_bearer(token)).json()
        assert me["email"] == "dev@x.com"
        assert me["role"] == "operator" == session_me["role"]
        assert me["tenant"] == session_me["tenant"]   # same tenant as their login
        assert me["auth_method"] == "pat"

        # And the PAT works on a normal authed endpoint.
        assert ctx.get("/api/suites", headers=_bearer(token)).status_code == 200

    def test_operator_pat_can_write(self, ctx):
        csrf = _signup(ctx)
        token = ctx.post("/api/settings/tokens", json={"name": "w"},
                         headers={"X-CSRF-Token": csrf}).json()["token"]
        # operator-gated mutation works under the PAT (no CSRF needed for bearer)
        r = ctx.post("/api/agents/catalog", headers=_bearer(token),
                     json={"agent_id": "a1", "variant": "reference"})
        assert r.status_code in (200, 201)

    def test_plaintext_never_returned_after_creation(self, ctx):
        csrf = _signup(ctx)
        created = ctx.post("/api/settings/tokens", json={"name": "once"},
                           headers={"X-CSRF-Token": csrf}).json()
        token = created["token"]
        listed = ctx.get("/api/settings/tokens", headers=_bearer(token)).json()
        assert listed["tokens"], "token should be listed"
        for t in listed["tokens"]:
            assert "token" not in t                  # never the plaintext
            assert t["masked"].startswith("agt_…")
            assert token not in str(t)               # not leaked anywhere

    def test_revocation_is_immediate(self, ctx):
        csrf = _signup(ctx)
        created = ctx.post("/api/settings/tokens", json={"name": "tmp"},
                           headers={"X-CSRF-Token": csrf}).json()
        token, tid = created["token"], created["id"]
        assert ctx.get("/api/me", headers=_bearer(token)).status_code == 200
        # revoke (session) — then the PAT is rejected immediately, even though a
        # session cookie is present (explicit bearer must be valid on its own).
        assert ctx.delete(f"/api/settings/tokens/{tid}",
                          headers={"X-CSRF-Token": csrf}).status_code == 200
        assert ctx.get("/api/me", headers=_bearer(token)).status_code == 401

    def test_shared_admin_token_still_works(self, ctx):
        # precedence: the configured shared/admin token path is unaffected
        assert ctx.get("/api/me", headers=_bearer("adm")).json()["role"] == "admin"

    def test_bad_pat_rejected(self, ctx):
        assert ctx.get("/api/me", headers=_bearer("agt_not_a_real_token")
                       ).status_code == 401

    def test_tokens_endpoint_requires_user_account(self, ctx):
        # the shared/config token has no user identity → can't own PATs
        r = ctx.post("/api/settings/tokens", json={"name": "x"},
                     headers=_bearer("adm"))
        assert r.status_code == 401


class TestPatTenantIsolation:
    def test_pat_cannot_read_another_tenant(self, ctx):
        # seed a suite into the DEFAULT tenant
        from tests.test_executor import load_pilot
        suite_id = load_pilot(ctx.reg)

        engine = ctx.reg.engine  # global engine where users + PATs live
        users = UserStore(engine)
        users.create_user("a@x.com", "password123", role="operator", tenant="default")
        users.create_user("b@x.com", "password123", role="operator", tenant="t2")
        pats = PatStore(engine)
        tok_default = pats.create(user_email="a@x.com", tenant="default",
                                  role="operator", name="d")["token"]
        tok_t2 = pats.create(user_email="b@x.com", tenant="t2",
                             role="operator", name="t")["token"]

        # default-tenant PAT sees the seeded suite
        d = ctx.get("/api/suites", headers=_bearer(tok_default)).json()
        assert any(s.get("suite_id") == suite_id for s in d)
        # t2 PAT is scoped to its own (separate) workspace — must NOT see it
        t = ctx.get("/api/suites", headers=_bearer(tok_t2)).json()
        assert not any(s.get("suite_id") == suite_id for s in t)
