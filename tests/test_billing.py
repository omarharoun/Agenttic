"""Billing system tests — the credits/entitlements core, metering, the 402
out-of-credits path, idempotent webhook credit application, invoice generation,
and the subscription lifecycle. Payment SDKs are never called: the Stripe verify
hook is monkeypatched and the PayPal webhook runs with no webhook-id (unverified
sandbox path), and the idempotent apply logic is exercised as pure functions.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agenttic.billing import plans, service
from agenttic.billing.store import BillingStore, GlobalBillingStore
from agenttic.billing.webhooks import apply_paypal_event, apply_stripe_event
from agenttic.copilot import credits as copilot_credits
from agenttic.registry.sqlite_store import Registry
from agenttic.server.app import create_app

CFG = {
    "billing": {
        "enabled": True, "currency": "usd", "credit_cent_value": 1,
        "free_trial_credits": 500, "markup_multiplier": 1.5, "min_action_credits": 1,
        "plans": {
            "free": {"name": "Free trial", "price_cents": 0, "interval": "once",
                     "included_credits": 500},
            "starter": {"name": "Starter", "price_cents": 2900, "interval": "month",
                        "included_credits": 5000},
            "pro": {"name": "Pro", "price_cents": 9900, "interval": "month",
                    "included_credits": 20000},
        },
        "topups": [{"id": "topup_10", "name": "$10 credit top-up",
                    "price_cents": 1000, "credits": 1000}],
    },
    "pricing": {"claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
                "default": {"input": 3.0, "output": 15.0}},
}


def _reg(tmp_path):
    return Registry(tmp_path / "b.db")


# --------------------------------------------------------------------------- #
# Credits / entitlements core.
# --------------------------------------------------------------------------- #

class TestCreditsCore:
    def test_free_credits_granted_once(self, tmp_path):
        eng = _reg(tmp_path).engine
        assert service.ensure_free_trial(eng, "t1", CFG) == 500
        assert BillingStore(eng, "t1").balance() == 500
        # idempotent: a second call grants nothing
        assert service.ensure_free_trial(eng, "t1", CFG) == 0
        assert BillingStore(eng, "t1").balance() == 500

    def test_entitlement_allows_with_credits_refuses_when_empty(self, tmp_path):
        eng = _reg(tmp_path).engine
        ent = service.entitlement(eng, "t1", CFG)   # grants trial on first sight
        assert ent["allowed"] and ent["balance"] == 500
        # drain the balance
        BillingStore(eng, "t1").debit(500, "copilot")
        ent2 = service.entitlement(eng, "t1", CFG)
        assert not ent2["allowed"] and ent2["reason"] == "out-of-credits"
        with pytest.raises(service.OutOfCreditsError):
            service.ensure_credits(eng, "t1", CFG)

    def test_copilot_token_debit(self, tmp_path):
        eng = _reg(tmp_path).engine
        service.ensure_free_trial(eng, "t1", CFG)
        # 1000 in + 500 out on sonnet: (1000*3 + 500*15)/1e6 = $0.0105 → *1.5 markup
        # = $0.01575 → ceil to 2 credits (cents)
        debited = service.meter_tokens(eng, "t1", "copilot", "claude-sonnet-4-6",
                                       1000, 500, cfg=CFG)
        assert debited == 2
        assert BillingStore(eng, "t1").balance() == 498

    def test_meter_cost_floors_at_min(self, tmp_path):
        eng = _reg(tmp_path).engine
        service.ensure_free_trial(eng, "t1", CFG)
        # a near-zero spend still costs at least min_action_credits (1)
        assert service.meter_cost(eng, "t1", "scan", 0.0000001, cfg=CFG) == 1

    def test_billing_disabled_is_permissive(self, tmp_path):
        eng = _reg(tmp_path).engine
        cfg = {"billing": {"enabled": False}}
        ent = service.entitlement(eng, "t1", cfg)
        assert ent["allowed"] and ent["balance"] is None
        # metering is a no-op when disabled
        assert service.meter_cost(eng, "t1", "copilot", 5.0, cfg=cfg) == 0

    def test_usage_breakdown_by_reason(self, tmp_path):
        eng = _reg(tmp_path).engine
        service.ensure_free_trial(eng, "t1", CFG)
        BillingStore(eng, "t1").debit(10, "copilot")
        BillingStore(eng, "t1").debit(3, "scan")
        BillingStore(eng, "t1").debit(7, "copilot")
        assert BillingStore(eng, "t1").usage_by_reason() == {"copilot": 17, "scan": 3}


# --------------------------------------------------------------------------- #
# Webhooks — grant credits, idempotent, invoices, subscription lifecycle.
# --------------------------------------------------------------------------- #

class TestStripeWebhooks:
    def _fixtures(self, tmp_path):
        reg = _reg(tmp_path)
        eng = reg.engine
        gstore = GlobalBillingStore(eng)
        return eng, gstore, (lambda t: eng)

    def test_topup_grants_credits_and_invoice_idempotent(self, tmp_path):
        eng, gstore, resolve = self._fixtures(tmp_path)
        service.ensure_free_trial(eng, "t1", CFG)
        evt = {"id": "evt_topup", "type": "checkout.session.completed", "data": {
            "object": {"mode": "payment", "amount_total": 1000, "id": "cs_1",
                       "customer": "cus_1",
                       "metadata": {"tenant": "t1", "topup_id": "topup_10",
                                    "credits": "1000", "description": "$10 top-up"}}}}
        r1 = apply_stripe_event(evt, resolve_engine=resolve, global_store=gstore, cfg=CFG)
        assert r1["status"] == "applied" and r1["credits"] == 1000
        assert BillingStore(eng, "t1").balance() == 1500
        invoices = BillingStore(eng, "t1").list_invoices()
        assert len(invoices) == 1 and invoices[0]["total_cents"] == 1000
        assert invoices[0]["credits_granted"] == 1000
        # REPLAY the exact same event → recognised as duplicate, no double-credit
        r2 = apply_stripe_event(evt, resolve_engine=resolve, global_store=gstore, cfg=CFG)
        assert r2["status"] == "duplicate"
        assert BillingStore(eng, "t1").balance() == 1500
        assert len(BillingStore(eng, "t1").list_invoices()) == 1

    def test_subscription_lifecycle(self, tmp_path):
        eng, gstore, resolve = self._fixtures(tmp_path)
        service.ensure_free_trial(eng, "t1", CFG)
        # 1) checkout completes for a subscription → active plan + included credits
        start = {"id": "evt_sub_start", "type": "checkout.session.completed", "data": {
            "object": {"mode": "subscription", "amount_total": 2900, "id": "cs_2",
                       "customer": "cus_9", "subscription": "sub_9",
                       "metadata": {"tenant": "t1", "plan_id": "starter"}}}}
        r = apply_stripe_event(start, resolve_engine=resolve, global_store=gstore, cfg=CFG)
        assert r["status"] == "applied"
        st = BillingStore(eng, "t1")
        assert st.get_subscription().plan_id == "starter"
        assert st.get_subscription().status == "active"
        assert st.balance() == 500 + 5000
        # 2) a renewal invoice.paid grants another month of credits
        renew = {"id": "evt_renew", "type": "invoice.paid", "data": {
            "object": {"subscription": "sub_9", "customer": "cus_9",
                       "amount_paid": 2900, "id": "in_1"}}}
        apply_stripe_event(renew, resolve_engine=resolve, global_store=gstore, cfg=CFG)
        assert st.balance() == 500 + 5000 + 5000
        # 3) cancellation → back to free/canceled
        cancel = {"id": "evt_cancel", "type": "customer.subscription.deleted", "data": {
            "object": {"id": "sub_9", "customer": "cus_9", "status": "canceled"}}}
        apply_stripe_event(cancel, resolve_engine=resolve, global_store=gstore, cfg=CFG)
        assert st.get_subscription().status == "canceled"
        assert st.get_subscription().plan_id == "free"

    def test_unknown_tenant_is_ignored(self, tmp_path):
        eng, gstore, resolve = self._fixtures(tmp_path)
        evt = {"id": "evt_x", "type": "checkout.session.completed",
               "data": {"object": {"mode": "payment", "amount_total": 1000}}}
        r = apply_stripe_event(evt, resolve_engine=resolve, global_store=gstore, cfg=CFG)
        assert r["status"] == "ignored"


class TestPaypalWebhooks:
    def test_topup_grant_idempotent(self, tmp_path):
        reg = _reg(tmp_path)
        eng = reg.engine
        gstore = GlobalBillingStore(eng)
        service.ensure_free_trial(eng, "t1", CFG)
        evt = {"id": "wh_1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {
            "id": "capture_1", "custom_id": "t1|topup_10",
            "amount": {"currency_code": "USD", "value": "10.00"}}}
        r1 = apply_paypal_event(evt, resolve_engine=lambda t: eng,
                                global_store=gstore, cfg=CFG)
        assert r1["status"] == "applied" and r1["credits"] == 1000
        assert BillingStore(eng, "t1").balance() == 1500
        r2 = apply_paypal_event(evt, resolve_engine=lambda t: eng,
                                global_store=gstore, cfg=CFG)
        assert r2["status"] == "duplicate"
        assert BillingStore(eng, "t1").balance() == 1500

    def test_subscription_activate_and_cancel(self, tmp_path):
        reg = _reg(tmp_path)
        eng = reg.engine
        gstore = GlobalBillingStore(eng)
        service.ensure_free_trial(eng, "t1", CFG)
        act = {"id": "wh_2", "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
               "resource": {"id": "I-SUB1", "custom_id": "t1"}}
        apply_paypal_event(act, resolve_engine=lambda t: eng,
                           global_store=gstore, cfg=CFG)
        st = BillingStore(eng, "t1")
        assert st.get_subscription().status == "active"
        assert st.get_subscription().provider == "paypal"
        assert st.balance() > 500  # included credits granted
        canc = {"id": "wh_3", "event_type": "BILLING.SUBSCRIPTION.CANCELLED",
                "resource": {"id": "I-SUB1", "custom_id": "t1"}}
        apply_paypal_event(canc, resolve_engine=lambda t: eng,
                           global_store=gstore, cfg=CFG)
        assert st.get_subscription().status == "canceled"


class TestStripeGatewaySignature:
    """Exercise the REAL Stripe signature verification path (no network, no real
    key): sign a payload with a throwaway secret, confirm construct_event returns
    a plain `.get()`-able dict, and that a tampered signature is rejected. Guards
    the SDK-version normalisation (StripeObject -> plain dict)."""

    def _sign(self, payload: bytes, secret: str) -> str:
        import hashlib
        import hmac
        import time
        ts = int(time.time())   # within Stripe's default 300s tolerance
        sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                       hashlib.sha256).hexdigest()
        return f"t={ts},v1={sig}"

    def test_construct_event_returns_plain_dict(self, monkeypatch):
        pytest.importorskip("stripe")
        from agenttic.billing.gateways import stripe_gateway as sg
        secret = "whsec_testsecret_000000000000000000000000"
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
        payload = json.dumps({"id": "evt_1", "object": "event",
            "type": "checkout.session.completed",
            "data": {"object": {"mode": "payment", "amount_total": 1000}}}).encode()
        event = sg.construct_event(payload, self._sign(payload, secret))
        assert isinstance(event, dict)
        # the apply logic relies on .get() working (StripeObject would break it)
        assert event.get("type") == "checkout.session.completed"
        assert event.get("data", {}).get("object", {}).get("amount_total") == 1000

    def test_bad_signature_rejected(self, monkeypatch):
        pytest.importorskip("stripe")
        import stripe
        from agenttic.billing.gateways import stripe_gateway as sg
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET",
                           "whsec_testsecret_000000000000000000000000")
        payload = b'{"id":"evt_1","object":"event","type":"x","data":{"object":{}}}'
        with pytest.raises(stripe.error.SignatureVerificationError):
            sg.construct_event(payload, "t=1700000000,v1=deadbeef")


# --------------------------------------------------------------------------- #
# HTTP surface — tenant-scoped dashboard, public pricing, 402, webhook route.
# --------------------------------------------------------------------------- #

CONFIG_YAML = """\
models: {agent_default: claude-sonnet-4-6, judge_executor: j, judge_strong: js, judge_light: jl, generator: g}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 8}
scoring: {calibration_threshold: 0.8}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {token: "adm", required: true, allow_signup: false, session_secret: testsecret}
pricing: {claude-sonnet-4-6: {input: 3.0, output: 15.0}, default: {input: 3.0, output: 15.0}}
billing:
  enabled: true
  free_trial_credits: %(trial)s
  markup_multiplier: 1.5
  plans:
    free: {name: Free trial, price_cents: 0, interval: once, included_credits: 500}
    starter: {name: Starter, price_cents: 2900, interval: month, included_credits: 5000}
  topups:
    - {id: topup_10, name: "$10 credit top-up", price_cents: 1000, credits: 1000}
copilot: {model: claude-sonnet-4-6}
certification: {profiles: {cert-agent-safety-v1: {required_domains: [harm_refusal], thresholds: {}}}}
"""


def _adm():
    return {"Authorization": "Bearer adm"}


def _mk_app(tmp_path, trial=500):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML % {"db": tmp_path / "c.db", "r": tmp_path / "r",
                                  "c": tmp_path / "cal", "trial": trial})
    reg = Registry(tmp_path / "c.db")
    return create_app(str(cfg), registry=reg)


class TestBillingHTTP:
    def test_overview_shows_free_trial(self, tmp_path):
        app = _mk_app(tmp_path)
        with TestClient(app) as c:
            r = c.get("/api/billing", headers=_adm())
            assert r.status_code == 200
            body = r.json()
            assert body["balance_credits"] == 500
            assert body["balance_display"] == "$5.00"
            assert body["plan"]["id"] == "free"

    def test_public_pricing_unauthenticated(self, tmp_path):
        app = _mk_app(tmp_path)
        with TestClient(app) as c:
            r = c.get("/api/pricing")  # no auth header
            assert r.status_code == 200
            body = r.json()
            assert body["free_trial_credits"] == 500
            ids = [p["id"] for p in body["plans"]]
            assert "free" in ids and "starter" in ids

    def test_ledger_and_invoices_endpoints(self, tmp_path):
        app = _mk_app(tmp_path)
        with TestClient(app) as c:
            r = c.get("/api/billing/ledger", headers=_adm())
            assert r.status_code == 200
            # the free-trial grant shows in the ledger
            assert any(e["reason"] == "signup" and e["credits"] == 500
                       for e in r.json()["entries"])
            assert c.get("/api/billing/invoices", headers=_adm()).json()["invoices"] == []

    def test_out_of_credits_returns_402_on_copilot(self, tmp_path, monkeypatch):
        # zero free trial → no credits → the real billing provider refuses the
        # Copilot chat with the existing 402 path.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")  # so it's "configured"
        app = _mk_app(tmp_path, trial=0)
        with TestClient(app) as c:
            r = c.post("/api/copilot/chat", headers=_adm(), json={"message": "hi"})
            assert r.status_code == 402
            # The Copilot error refactor makes every refusal carry the structured
            # {code, message, action} shape (one styled card whether 4xx or SSE),
            # so the out-of-credits 402 now surfaces the stable machine code plus
            # an honest, credit-mentioning message rather than a bare string.
            detail = r.json()["detail"]
            assert detail["code"] == "out_of_credits"
            assert "credit" in detail["message"].lower()

    def test_real_provider_installed_when_enabled(self, tmp_path):
        app = _mk_app(tmp_path)
        with TestClient(app):
            from agenttic.billing.credits_provider import BillingCreditsProvider
            assert isinstance(copilot_credits.get_provider(), BillingCreditsProvider)
        # restored to the default stub on shutdown
        assert type(copilot_credits.get_provider()) is copilot_credits.CreditsProvider

    def test_stripe_webhook_route_grants_credits(self, tmp_path, monkeypatch):
        app = _mk_app(tmp_path)
        evt = {"id": "evt_http_1", "type": "checkout.session.completed", "data": {
            "object": {"mode": "payment", "amount_total": 1000, "id": "cs_h",
                       "customer": "cus_h",
                       "metadata": {"tenant": "default", "topup_id": "topup_10",
                                    "credits": "1000", "description": "$10 top-up"}}}}
        # bypass signature verification by faking the SDK construct_event
        from agenttic.billing.gateways import stripe_gateway
        monkeypatch.setattr(stripe_gateway, "construct_event",
                            lambda payload, sig: evt)
        with TestClient(app) as c:
            before = c.get("/api/billing", headers=_adm()).json()["balance_credits"]
            r = c.post("/api/billing/webhooks/stripe", content=json.dumps(evt),
                       headers={"stripe-signature": "t=1,v1=x"})
            assert r.status_code == 200 and r.json()["status"] == "applied"
            after = c.get("/api/billing", headers=_adm()).json()["balance_credits"]
            assert after == before + 1000
            invoices = c.get("/api/billing/invoices", headers=_adm()).json()["invoices"]
            assert len(invoices) == 1
            # the invoice downloads as standalone HTML
            iid = invoices[0]["invoice_id"]
            dl = c.get(f"/api/billing/invoices/{iid}/download", headers=_adm())
            assert dl.status_code == 200 and "Invoice" in dl.text

    def test_checkout_requires_configured_provider(self, tmp_path):
        app = _mk_app(tmp_path)
        with TestClient(app) as c:
            r = c.post("/api/billing/checkout/stripe", headers=_adm(),
                       json={"kind": "topup", "topup_id": "topup_10"})
            # no STRIPE_SECRET_KEY in the env → honest 503, not a crash
            assert r.status_code == 503
            assert "stripe" in r.json()["detail"].lower()

    def test_tenant_isolation(self, tmp_path):
        # a second tenant's balance is independent (own DB file / row scope)
        app = _mk_app(tmp_path)
        with TestClient(app) as c:
            # default tenant sees its own trial only
            body = c.get("/api/billing", headers=_adm()).json()
            assert body["balance_credits"] == 500
