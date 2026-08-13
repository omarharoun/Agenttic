"""Revoking a passport inside the 60s revocation-cache TTL (RECEIPT-SCHEMA.md
§3 step 5) — both halves of the split, demonstrated rather than asserted.

A cached revocation status is a deliberate trade: one network call per passport
per minute instead of one per tool call, paid for with up to 60s of staleness.
This test drives a fake clock and a call-counting status fetcher so the trade is
*visible*: the read endpoint keeps answering off a stale "active" for the rest
of the TTL, while the irreversible delete — which pays a live round-trip — stops
immediately. Both facts are observed, including the uncomfortable one.

The tool is built here rather than taken from ``examples.receipt_gated_tool``
because the demo's ``delete_customer`` returns ``{"deleted": id}`` and mutates
nothing: with no state, "the customer was not deleted" is unfalsifiable. Same
middleware, same declared schema, same tool names — plus a dict that a blocked
call must leave alone. A 403 with the side effect already applied is exactly the
failure this whole gate exists to prevent, so it has to be observable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from agenttic.gate.receipt import Principal, issue_tool_access_receipt
from agenttic.passport.keys import PassportKeyManager, generate_key
from examples.receipt_gated_tool import CUSTOMER_ID_SCHEMA

CFG = load_config("config.yaml")

T0 = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
PASSPORT = "pp-revoke-me"


class _Clock:
    """The injected clock. Revocation is time-shaped; a test that cannot move
    time can only assert the cache exists, never what it costs."""

    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class _Status:
    """The injected status fetcher, counting its calls — "served from the cache"
    is then an observation, not a claim about code we hope ran."""

    def __init__(self) -> None:
        self.calls = 0
        self.status = "active"

    def __call__(self, url: str) -> dict:
        self.calls += 1
        return {"status": self.status}


def _setup():
    clock, status = _Clock(), _Status()
    keys = PassportKeyManager(CFG, private_key=generate_key())
    customers = {"c-1": "Ada Lovelace", "c-2": "Grace Hopper"}
    gate = dict(jwks=keys.jwks,
                nonce_store=InMemoryNonceStore(now=clock),
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


def _headers(keys, clock, tool, action_class, bound_values=None) -> dict:
    # A fresh receipt per call: the nonce is single-use, so reusing one would
    # fail as a replay and hide whatever this step was meant to show.
    receipt = issue_tool_access_receipt(
        keys, tool=tool, action_class=action_class,
        params_schema=CUSTOMER_ID_SCHEMA, bound_values=bound_values,
        passport_id=PASSPORT, passport_hash="a1c9",
        principal=Principal(id="sub:okta|jane.doe", via=["agent:triage-bot"]),
        gateway_id="gw:test", decision_id="decision:test", policy_hash="e0aa",
        now=clock())
    return {HEADER_NAME: encode_receipt_header(receipt.model_dump(mode="json"))}


def test_revocation_blocks_the_irreversible_call_inside_the_cache_ttl():
    client, clock, status, keys, customers = _setup()

    # 1. Passport active: the read succeeds and the status is fetched once,
    #    populating the cache at T0.
    r = client.get("/customers/c-1",
                   headers=_headers(keys, clock, "read_customer", "read"))
    assert r.status_code == 200, r.text
    assert status.calls == 1

    # Positive control: while active, the delete really does delete. Without
    # this, "c-1 is still there" later would prove nothing about the gate — an
    # endpoint that never deletes anything passes that assertion for free.
    clock.advance(5)
    r = client.delete("/customers/c-2",
                      headers=_headers(keys, clock, "delete_customer",
                                       "irreversible", {"customer_id": "c-2"}))
    assert r.json() == {"deleted": "c-2"}
    assert "c-2" not in customers
    assert status.calls == 2  # irreversible always pays the live round-trip

    # 2. The passport is revoked.
    status.status = "revoked"

    # 3. Still inside the 60s TTL. The read STILL PASSES, off a status that is
    #    now wrong, and the fetcher is not called again. This is the honest cost
    #    of caching, not a bug: for up to 60s a revoked agent keeps reading. The
    #    trade is bounded and deliberate — one fetch per passport per minute
    #    instead of one per call — and it is why the split in step 5 exists.
    clock.advance(25)  # T0+30, inside the 60s TTL of the entry cached at T0
    r = client.get("/customers/c-1",
                   headers=_headers(keys, clock, "read_customer", "read"))
    assert r.status_code == 200, "stale-active read inside the TTL — the cost"
    assert status.calls == 2, "no fetch: the cache answered"

    # 4. Same instant, same stale cache: the irreversible delete is BLOCKED,
    #    because it does not consult the cache at all.
    r = client.delete("/customers/c-1",
                      headers=_headers(keys, clock, "delete_customer",
                                       "irreversible", {"customer_id": "c-1"}))
    assert r.status_code == 403
    assert r.json()["detail"] == "agent passport revoked"
    assert status.calls == 3, "the live path fetched"
    assert customers["c-1"] == "Ada Lovelace"  # 403 AND no side effect

    # ...and that live check did not populate the cache. If it had written
    # ("revoked", T0+30), this read would fail; if it had refreshed the entry's
    # timestamp, the TTL would slide. It still answers "active" off the entry
    # from T0, with no new fetch — so one irreversible check can neither poison
    # nor extend what the normal path trusts.
    clock.advance(20)  # T0+50, still inside the entry's TTL
    r = client.get("/customers/c-1",
                   headers=_headers(keys, clock, "read_customer", "read"))
    assert r.status_code == 200
    assert status.calls == 3

    # 5. Past the TTL, the normal path catches up: refetch, and now it blocks.
    clock.advance(11)  # T0+61
    r = client.get("/customers/c-1",
                   headers=_headers(keys, clock, "read_customer", "read"))
    assert r.status_code == 403
    assert r.json()["detail"] == "agent passport revoked"
    assert status.calls == 4, "TTL expired: fetched again"

    # 6. And the customer the blocked calls targeted was never touched.
    r = client.delete("/customers/c-1",
                      headers=_headers(keys, clock, "delete_customer",
                                       "irreversible", {"customer_id": "c-1"}))
    assert r.status_code == 403
    assert customers == {"c-1": "Ada Lovelace"}
