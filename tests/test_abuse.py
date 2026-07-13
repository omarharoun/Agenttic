"""Abuse controls for the cost-bearing endpoints (:mod:`agenttic.server.abuse`).

Asserts the hardening structurally:
* each layer trips at its configured threshold (per-IP, per-tenant per minute),
* the server-wide daily ceiling caps AGGREGATE spend across IPs/tenants,
* a request already refused by a narrower layer does NOT drain the global budget,
* signup is throttled per IP AND the free-account grant is idempotent per email
  (can't be farmed by re-signing-up the same address),
* every knob is 0 = off (legitimate use unaffected when unconfigured).
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agenttic.registry.sqlite_store import Registry
from agenttic.server import abuse
from agenttic.server.app import create_app


def _req(cfg, ip="9.9.9.9", tenant="t1"):
    return NS(state=NS(cfg=cfg, tenant=tenant), client=NS(host=ip),
              app=NS(state=NS(cfg=cfg)))


@pytest.fixture(autouse=True)
def _clean():
    abuse.reset_abuse()
    yield
    abuse.reset_abuse()


# --------------------------------------------------------------------------- #
# 1. Cost-endpoint guard — per-IP / per-tenant / global-day layers.
# --------------------------------------------------------------------------- #


class TestCostGuard:
    def test_off_by_default(self):
        # no abuse block => never raises, however many times we call it
        for _ in range(50):
            abuse.guard_cost_endpoint(_req({}), "scan")

    def test_per_ip_trips_at_threshold(self):
        cfg = {"abuse": {"scan": {"per_ip_per_minute": 2}}}
        abuse.guard_cost_endpoint(_req(cfg), "scan")   # 1 ok
        abuse.guard_cost_endpoint(_req(cfg), "scan")   # 2 ok
        with pytest.raises(HTTPException) as ei:
            abuse.guard_cost_endpoint(_req(cfg), "scan")  # 3 -> 429
        assert ei.value.status_code == 429
        assert ei.value.detail["code"] == "rate_limited"

    def test_per_ip_isolates_distinct_ips(self):
        cfg = {"abuse": {"scan": {"per_ip_per_minute": 1}}}
        abuse.guard_cost_endpoint(_req(cfg, ip="1.1.1.1"), "scan")
        # a different IP is under its own budget
        abuse.guard_cost_endpoint(_req(cfg, ip="2.2.2.2"), "scan")
        with pytest.raises(HTTPException):
            abuse.guard_cost_endpoint(_req(cfg, ip="1.1.1.1"), "scan")

    def test_per_tenant_trips_even_across_ips(self):
        # per-IP off, per-tenant on: one workspace hammering from many IPs is still bounded
        cfg = {"abuse": {"scan": {"per_tenant_per_minute": 2}}}
        abuse.guard_cost_endpoint(_req(cfg, ip="1.1.1.1", tenant="acme"), "scan")
        abuse.guard_cost_endpoint(_req(cfg, ip="2.2.2.2", tenant="acme"), "scan")
        with pytest.raises(HTTPException) as ei:
            abuse.guard_cost_endpoint(_req(cfg, ip="3.3.3.3", tenant="acme"), "scan")
        assert ei.value.status_code == 429

    def test_global_daily_ceiling_caps_aggregate(self):
        # rotating BOTH ip and tenant still can't exceed the server-wide daily cap
        cfg = {"abuse": {"certify": {"global_per_day": 3}}}
        for i in range(3):
            abuse.guard_cost_endpoint(
                _req(cfg, ip=f"10.0.0.{i}", tenant=f"t{i}"), "certify")
        with pytest.raises(HTTPException) as ei:
            abuse.guard_cost_endpoint(_req(cfg, ip="10.0.0.9", tenant="t9"), "certify")
        assert ei.value.status_code == 429
        assert "everyone" in ei.value.detail["message"].lower()

    def test_refused_request_does_not_drain_global_budget(self):
        # per_ip=1 and global=1: a request blocked by the IP layer must NOT have
        # consumed the single global slot — so a fresh IP is what trips global.
        cfg = {"abuse": {"scan": {"per_ip_per_minute": 1, "global_per_day": 1}}}
        abuse.guard_cost_endpoint(_req(cfg, ip="1.1.1.1"), "scan")   # ok: ip+global=1
        with pytest.raises(HTTPException):
            abuse.guard_cost_endpoint(_req(cfg, ip="1.1.1.1"), "scan")  # blocked by IP
        # global was consumed exactly once → a new IP now trips the global ceiling
        with pytest.raises(HTTPException) as ei:
            abuse.guard_cost_endpoint(_req(cfg, ip="2.2.2.2"), "scan")
        assert "everyone" in ei.value.detail["message"].lower()

    def test_per_action_budgets_are_independent(self):
        cfg = {"abuse": {"scan": {"per_ip_per_minute": 1},
                         "certify": {"per_ip_per_minute": 1}}}
        abuse.guard_cost_endpoint(_req(cfg), "scan")
        abuse.guard_cost_endpoint(_req(cfg), "certify")  # different action, own budget
        with pytest.raises(HTTPException):
            abuse.guard_cost_endpoint(_req(cfg), "scan")


# --------------------------------------------------------------------------- #
# 2. Signup guard — per-IP throttle (unit).
# --------------------------------------------------------------------------- #


class TestSignupGuard:
    def test_off_by_default(self):
        for _ in range(20):
            abuse.guard_signup(_req({}))

    def test_per_ip_throttle_trips(self):
        cfg = {"abuse": {"signup": {"per_ip_per_hour": 2}}}
        abuse.guard_signup(_req(cfg))
        abuse.guard_signup(_req(cfg))
        with pytest.raises(HTTPException) as ei:
            abuse.guard_signup(_req(cfg))
        assert ei.value.status_code == 429


# --------------------------------------------------------------------------- #
# 3. Signup abuse-resistance end-to-end (real app).
# --------------------------------------------------------------------------- #


CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, allow_signup: true, signup_role: admin, session_secret: testsecret}
abuse: {signup: {per_ip_per_hour: %(sc)s}}
"""


def _client(tmp_path, signup_cap=100):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c", "sc": signup_cap})
    return TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db")))


class TestSignupEndpoint:
    def test_free_account_grant_is_idempotent_per_email(self, tmp_path):
        # signing up the same email twice can't farm a second account/tenant
        with _client(tmp_path) as c:
            r1 = c.post("/api/auth/signup",
                        json={"email": "a@x.com", "password": "password123"})
            assert r1.status_code == 200 and r1.json()["tenant"]
            r2 = c.post("/api/auth/signup",
                        json={"email": "a@x.com", "password": "password123"})
            assert r2.status_code == 409  # one grant per email — not farmable
            # and it certainly didn't mint a second tenant
            assert "tenant" not in r2.json()

    def test_signup_throttled_per_ip(self, tmp_path):
        # fresh addresses from one network are bounded by the per-IP hourly cap
        with _client(tmp_path, signup_cap=2) as c:
            codes = [c.post("/api/auth/signup",
                            json={"email": f"u{i}@x.com", "password": "password123"}
                            ).status_code for i in range(3)]
        assert codes[:2] == [200, 200]
        assert codes[2] == 429
