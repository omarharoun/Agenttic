"""Adversarial slice 4 — revocation, ordering, and fail-open probes.

Every test here tries to make ``delete_customer`` actually delete a customer
without a valid, current, single-use receipt, or to make a rejected receipt
leak state (a burnt nonce, a network call) it should never have touched.

The endpoints mutate a real dict, so "blocked" means the dict is untouched, not
merely that a 403 came back.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenttic.config import load_config
from agenttic.gate.middleware import (
    HEADER_NAME,
    InMemoryNonceStore,
    RevocationCache,
    encode_receipt_header,
    require_receipt,
)
from agenttic.gate.receipt import (
    Principal,
    compute_bound_params,
    issue_tool_access_receipt,
)
from agenttic.passport.keys import PassportKeyManager, generate_key
from examples.receipt_gated_tool import CUSTOMER_ID_SCHEMA

CFG = load_config("config.yaml")

T0 = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
PASSPORT = "pp-attack-4"


class _Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class _Status:
    """Injected status fetcher. Counts calls, and can be made to lie, to return
    an off-schema body, or to blow up."""

    def __init__(self, status: str = "active") -> None:
        self.calls = 0
        self.status = status
        self.body: dict | None = None
        self.raises: Exception | None = None

    def __call__(self, url: str) -> dict:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.body if self.body is not None else {"status": self.status}


class _ExplodingNonceStore:
    def __init__(self) -> None:
        self.calls = 0

    def claim(self, nonce, expires_at) -> bool:
        self.calls += 1
        raise RuntimeError("nonce backend is down")


def _setup(*, status: _Status | None = None, nonce_store=None, jwks="real"):
    clock = _Clock()
    status = status or _Status()
    keys = PassportKeyManager(CFG, private_key=generate_key())
    customers = {"c-1": "Ada Lovelace", "c-2": "Grace Hopper"}
    gate = dict(jwks=keys.jwks if jwks == "real" else jwks,
                nonce_store=nonce_store or InMemoryNonceStore(now=clock),
                revocations=RevocationCache(fetcher=status, now=clock),
                now=clock)

    app = FastAPI()

    @app.get("/customers/{customer_id}")
    @require_receipt("read_customer", "read", CUSTOMER_ID_SCHEMA, **gate)
    def read_customer(customer_id: str):
        return {"customer_id": customer_id, "name": customers[customer_id]}

    @app.delete("/customers/{customer_id}")
    @require_receipt("delete_customer", "irreversible", CUSTOMER_ID_SCHEMA,
                     ["customer_id"], **gate)
    def delete_customer(customer_id: str):
        del customers[customer_id]  # the real, unrecoverable side effect
        return {"deleted": customer_id}

    return TestClient(app), clock, status, keys, customers


def _receipt(keys, clock, tool, action_class, bound_values=None, *,
             nonce: str | None = None, issued_at: datetime | None = None):
    r = issue_tool_access_receipt(
        keys, tool=tool, action_class=action_class,
        params_schema=CUSTOMER_ID_SCHEMA, bound_values=bound_values,
        passport_id=PASSPORT, passport_hash="a1c9",
        principal=Principal(id="sub:okta|jane.doe", via=["agent:triage-bot"]),
        gateway_id="gw:test", decision_id="decision:test", policy_hash="e0aa",
        now=issued_at or clock())
    if nonce is not None:
        # Re-mint on a chosen nonce so "the same nonce, on a valid receipt"
        # is testable. bound_params is nonce-salted, so it must be recomputed,
        # and the whole thing re-signed — no field survives unsigned.
        r.nonce = nonce
        if bound_values:
            r.bound_params = compute_bound_params(nonce, bound_values)
        r.signature = keys.sign(r.signing_input())
    return r


def _hdr(receipt_or_dict) -> dict:
    raw = (receipt_or_dict if isinstance(receipt_or_dict, dict)
           else receipt_or_dict.model_dump(mode="json"))
    return {HEADER_NAME: encode_receipt_header(raw)}


# --------------------------------------------------------------------------- #
# Revocation
# --------------------------------------------------------------------------- #


def test_revoked_passport_blocks_normal_action_on_a_cold_cache():
    """No cached entry to hide behind: the first read must fetch and refuse."""
    client, clock, status, keys, customers = _setup(status=_Status("revoked"))

    r = client.get("/customers/c-1",
                   headers=_hdr(_receipt(keys, clock, "read_customer", "read")))

    assert r.status_code == 403
    assert r.json()["detail"] == "agent passport revoked"
    assert status.calls == 1


def test_revoked_passport_blocks_irreversible_via_the_live_path():
    client, clock, status, keys, customers = _setup(status=_Status("revoked"))

    r = client.delete("/customers/c-1",
                      headers=_hdr(_receipt(keys, clock, "delete_customer",
                                            "irreversible",
                                            {"customer_id": "c-1"})))

    assert r.status_code == 403
    assert r.json()["detail"] == "agent passport revoked"
    assert customers == {"c-1": "Ada Lovelace", "c-2": "Grace Hopper"}


def test_irreversible_live_check_cannot_warm_the_cache_for_a_later_read():
    """The warming attack, in the direction the existing revocation test does
    not cover: get an irreversible call to cache "active" while the passport is
    still good, then revoke and ride that warmed entry on the normal path."""
    client, clock, status, keys, customers = _setup()

    # 1. An irreversible call while active. This is the only status fetch so
    #    far — if status_live wrote through, the cache now holds ("active", T0).
    r = client.delete("/customers/c-2",
                      headers=_hdr(_receipt(keys, clock, "delete_customer",
                                            "irreversible",
                                            {"customer_id": "c-2"})))
    assert r.status_code == 200
    assert status.calls == 1

    # 2. Revoke, and immediately take the cached path well inside the 60s TTL.
    status.status = "revoked"
    clock.advance(1)
    r = client.get("/customers/c-1",
                   headers=_hdr(_receipt(keys, clock, "read_customer", "read")))

    assert r.status_code == 403, "a live check warmed the cache — bypass"
    assert status.calls == 2, "the read had to fetch: nothing was cached for it"


# --------------------------------------------------------------------------- #
# Ordering — a doomed receipt must not burn its nonce or make a network call
# --------------------------------------------------------------------------- #


def test_bad_signature_burns_no_nonce_and_makes_no_network_call():
    client, clock, status, keys, customers = _setup()
    nonce = "attack4-shared-nonce-aa"
    values = {"customer_id": "c-1"}

    bad = _receipt(keys, clock, "delete_customer", "irreversible", values,
                   nonce=nonce).model_dump(mode="json")
    bad["signature"] = ("A" if bad["signature"][0] != "A" else "B") + bad["signature"][1:]

    r = client.delete("/customers/c-1", headers=_hdr(bad))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert status.calls == 0, "step 1 failed but the network was still hit"
    assert customers["c-1"] == "Ada Lovelace"

    # The same nonce, on a properly signed receipt, still works => step 6 never
    # ran. If the claim had happened before the signature check, this is a 403.
    good = _receipt(keys, clock, "delete_customer", "irreversible", values,
                    nonce=nonce)
    r = client.delete("/customers/c-1", headers=_hdr(good))
    assert r.status_code == 200, r.text
    assert status.calls == 1


def test_expired_receipt_burns_no_nonce_and_makes_no_network_call():
    client, clock, status, keys, customers = _setup()
    nonce = "attack4-shared-nonce-bb"
    values = {"customer_id": "c-1"}

    stale = _receipt(keys, clock, "delete_customer", "irreversible", values,
                     nonce=nonce)
    clock.advance(120)  # past the 30s irreversible TTL, past any skew

    r = client.delete("/customers/c-1", headers=_hdr(stale))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt expired"
    assert status.calls == 0
    assert customers["c-1"] == "Ada Lovelace"

    fresh = _receipt(keys, clock, "delete_customer", "irreversible", values,
                     nonce=nonce)
    r = client.delete("/customers/c-1", headers=_hdr(fresh))
    assert r.status_code == 200, "the doomed receipt consumed its nonce"
    assert status.calls == 1


def test_revocation_rejection_burns_no_nonce():
    """Step 5 fails after the network call but before the claim — a revoked
    passport must not let an attacker grief a nonce that is later re-issued."""
    client, clock, status, keys, customers = _setup(status=_Status("revoked"))
    nonce = "attack4-shared-nonce-cc"

    r = client.get("/customers/c-1",
                   headers=_hdr(_receipt(keys, clock, "read_customer", "read",
                                         nonce=nonce)))
    assert r.status_code == 403

    status.status = "active"
    clock.advance(90)  # past the TTL of the entry cached by the failed call
    r = client.get("/customers/c-1",
                   headers=_hdr(_receipt(keys, clock, "read_customer", "read",
                                         nonce=nonce)))
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# Fail-open probes
# --------------------------------------------------------------------------- #


def test_status_fetcher_raising_rejects_not_passes():
    status = _Status()
    status.raises = ConnectionError("status endpoint unreachable")
    client, clock, _s, keys, customers = _setup(status=status)

    r = client.delete("/customers/c-1",
                      headers=_hdr(_receipt(keys, clock, "delete_customer",
                                            "irreversible",
                                            {"customer_id": "c-1"})))
    assert r.status_code == 403
    assert customers["c-1"] == "Ada Lovelace"

    r = client.get("/customers/c-1",
                   headers=_hdr(_receipt(keys, clock, "read_customer", "read")))
    assert r.status_code == 403


def test_nonce_store_raising_rejects_not_passes():
    store = _ExplodingNonceStore()
    client, clock, status, keys, customers = _setup(nonce_store=store)

    r = client.delete("/customers/c-1",
                      headers=_hdr(_receipt(keys, clock, "delete_customer",
                                            "irreversible",
                                            {"customer_id": "c-1"})))
    assert r.status_code == 403
    assert store.calls == 1, "the claim was reached"
    assert customers["c-1"] == "Ada Lovelace", "executed despite a dead store"


@pytest.mark.parametrize("jwks", [{"keys": []}, None, lambda: {"keys": []}])
def test_empty_or_missing_jwks_rejects_everything(jwks):
    client, clock, status, keys, customers = _setup(jwks=jwks)

    r = client.delete("/customers/c-1",
                      headers=_hdr(_receipt(keys, clock, "delete_customer",
                                            "irreversible",
                                            {"customer_id": "c-1"})))
    assert r.status_code == 403
    assert customers["c-1"] == "Ada Lovelace"
    assert status.calls == 0


def test_status_body_without_a_status_key_is_treated_as_active():
    """check_status defaults a missing "status" key to "active"
    (verifier/sdk.py:151). A status endpoint that answers with any other JSON
    shape — a 404 body ``{"detail": ...}``, an error envelope, a proxy page —
    therefore reads as *not revoked*, and the irreversible action executes.

    This test documents the behaviour as it is; it asserts the side effect
    really happens so the report is not an opinion.
    """
    status = _Status()
    status.body = {"detail": "passport pp-attack-4 not found"}  # a real 404 body
    client, clock, _s, keys, customers = _setup(status=status)

    r = client.delete("/customers/c-1",
                      headers=_hdr(_receipt(keys, clock, "delete_customer",
                                            "irreversible",
                                            {"customer_id": "c-1"})))

    assert r.status_code == 200
    assert "c-1" not in customers  # the deletion actually happened
