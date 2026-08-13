"""Adversarial slice 2 — expiry/clock and action mismatch.

Every test here tries to make a protected action *execute its side effect*
without a receipt that authorises it. The tool under test keeps a real dict, so
"blocked" means the customer is still in it — a 403 with the deletion already
applied is the exact failure the gate exists to prevent, and it has to be
observable rather than asserted.

Two attacks are also *ordering* proofs (RECEIPT-SCHEMA.md §3): a receipt that
fails at step 1 or 2 must not have burned its nonce and must not have triggered
a network round-trip. Both are observed with a call-counting fetcher and by
re-presenting the same nonce on an otherwise-good receipt.

The attacker here never holds the gateway signing key. Everything signed below
is signed as the gateway would sign it (an attack that only works by forging a
signature is a key-compromise story, not a gate bypass).
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
from agenttic.gate.receipt import (
    Principal,
    ToolAccessReceipt,
    compute_bound_params,
    issue_tool_access_receipt,
)
from agenttic.passport.keys import PassportKeyManager, generate_key
from examples.receipt_gated_tool import CUSTOMER_ID_SCHEMA

CFG = load_config("config.yaml")

T0 = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
PASSPORT = "pp-attacker"
PRINCIPAL = Principal(id="sub:okta|jane.doe", via=["agent:triage-bot"])

# Same tool, same name, a *different* declared parameter schema. Action-shape
# binding is over the schema, so this must not match the real endpoint.
OTHER_SCHEMA = {
    "type": "object",
    "properties": {"customer_id": {"type": "integer"}},
    "required": ["customer_id"],
}


class _Clock:
    def __init__(self, t: datetime = T0) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class _Status:
    """Counts calls, so "the network was never reached" is an observation."""

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


def _mint(keys, *, tool="delete_customer", action_class="irreversible",
          params_schema=CUSTOMER_ID_SCHEMA, bound_values=None,
          now=T0, ttl_seconds=None) -> ToolAccessReceipt:
    if bound_values is None and action_class == "irreversible":
        bound_values = {"customer_id": "c-1"}
    return issue_tool_access_receipt(
        keys, tool=tool, action_class=action_class, params_schema=params_schema,
        bound_values=bound_values, ttl_seconds=ttl_seconds,
        passport_id=PASSPORT, passport_hash="a1c9", principal=PRINCIPAL,
        gateway_id="gw:test", decision_id="decision:test", policy_hash="e0aa",
        now=now)


def _hdr(receipt: ToolAccessReceipt | dict) -> dict:
    raw = (receipt.model_dump(mode="json")
           if isinstance(receipt, ToolAccessReceipt) else receipt)
    return {HEADER_NAME: encode_receipt_header(raw)}


def _resign(keys, receipt: ToolAccessReceipt, **overrides) -> ToolAccessReceipt:
    """Re-mint with fields the issuer controls, signed properly.

    Used only to construct receipts a *gateway* could legitimately have issued
    (a chosen nonce, a future not_before) — never to forge one.
    """
    data = receipt.model_dump(mode="json")
    data.pop("signature", None)
    data.update(overrides)
    fresh = ToolAccessReceipt.model_validate(data)
    fresh.signature = keys.sign(fresh.signing_input())
    return fresh


# --------------------------------------------------------------------------- #
# Expiry / clock.
# --------------------------------------------------------------------------- #


def test_expired_receipt_cannot_delete():
    """Attack: hold a legitimately issued receipt until after it expires."""
    client, clock, status, keys, customers = _setup()
    receipt = _mint(keys)  # irreversible -> 30s TTL

    clock.advance(31)
    r = client.delete("/customers/c-1", headers=_hdr(receipt))

    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt expired"
    assert customers["c-1"] == "Ada Lovelace"  # BLOCKED: no side effect


def test_expiry_boundary_is_exclusive_at_the_exact_instant():
    """Attack: land exactly on ``expires_at``. ``now >= expires_at`` rejects."""
    client, clock, status, keys, customers = _setup()
    receipt = _mint(keys)

    clock.t = receipt.expires_at  # to the microsecond
    r = client.delete("/customers/c-1", headers=_hdr(receipt))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt expired"
    assert customers["c-1"] == "Ada Lovelace"

    # Control: one microsecond earlier the same receipt really does delete, so
    # the assertion above is about the boundary and not about a dead endpoint.
    clock.t = receipt.expires_at - timedelta(microseconds=1)
    r = client.delete("/customers/c-1", headers=_hdr(receipt))
    assert r.json() == {"deleted": "c-1"}
    assert "c-1" not in customers


def test_expiry_edge_grants_no_skew_grace():
    """Attack: the contract says "5s skew on both edges" — try to spend that
    budget *past* expiry.

    The implementation applies skew only to the ``not_before`` edge
    (middleware.py:208) and rejects at ``now >= expires_at`` outright
    (middleware.py:210), matching RECEIPT-SCHEMA.md §2.4's explicit rules. So
    there is no post-expiry window: 1s, 4s and 5s past are all rejected.
    """
    client, clock, status, keys, customers = _setup()
    for late in (1, 4, 5, 4.999):
        receipt = _mint(keys, now=clock())
        clock.t = receipt.expires_at + timedelta(seconds=late)
        r = client.delete("/customers/c-1", headers=_hdr(receipt))
        assert r.status_code == 403, f"{late}s past expiry executed"
        assert r.json()["detail"] == "tool access receipt expired"
    assert customers["c-1"] == "Ada Lovelace"


def test_not_before_in_the_future_blocks():
    """Attack: present a pre-dated receipt before it is valid."""
    client, clock, status, keys, customers = _setup()
    receipt = _resign(keys, _mint(keys),
                      not_before=(T0 + timedelta(seconds=60)).isoformat(),
                      expires_at=(T0 + timedelta(seconds=600)).isoformat())

    r = client.delete("/customers/c-1", headers=_hdr(receipt))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt expired"  # not-yet-valid
    assert customers["c-1"] == "Ada Lovelace"


def test_not_before_skew_boundary_on_the_early_edge():
    """The early edge, both sides of it.

    5.001s early is rejected. Exactly 5s early is *accepted* — that is the
    documented calibration knob (skew, default 5s, both hosts' clocks differ),
    not a bypass: RECEIPT-SCHEMA.md §2.4 rejects on ``now < not_before - skew``,
    strictly. Recorded here so the size of the accepted window is pinned by a
    test rather than left to be discovered.
    """
    client, clock, status, keys, customers = _setup()
    nb = T0 + timedelta(seconds=60)
    exp = T0 + timedelta(seconds=600)

    clock.t = nb - timedelta(seconds=5, microseconds=1)
    r = client.delete("/customers/c-1", headers=_hdr(_resign(
        keys, _mint(keys), not_before=nb.isoformat(),
        expires_at=exp.isoformat())))
    assert r.status_code == 403
    assert customers["c-1"] == "Ada Lovelace"

    clock.t = nb - timedelta(seconds=5)  # exactly at the edge
    r = client.delete("/customers/c-1", headers=_hdr(_resign(
        keys, _mint(keys), not_before=nb.isoformat(),
        expires_at=exp.isoformat())))
    assert r.status_code == 200, "the 5s skew window is the documented knob"
    assert "c-1" not in customers


def test_extended_expires_at_fails_at_signature_before_the_expiry_check():
    """Attack: take an expired receipt and push ``expires_at`` an hour out.

    Must die at step 1, not step 2 — the timestamps are inside the signature.
    Proven three ways: the reason names the signature, the status fetcher was
    never called (step 5 unreached), and the nonce is still spendable (step 6
    unreached), so the forgery burned nothing.
    """
    client, clock, status, keys, customers = _setup()
    receipt = _mint(keys)
    clock.advance(31)  # genuinely expired

    raw = receipt.model_dump(mode="json")
    raw["expires_at"] = (T0 + timedelta(hours=1)).isoformat()
    r = client.delete("/customers/c-1", headers=_hdr(raw))

    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid", \
        "stopped at step 1 (signature), never reached step 2 (expiry)"
    assert customers["c-1"] == "Ada Lovelace"
    assert status.calls == 0, "no network round-trip for a receipt that failed"

    # The nonce was not consumed: a properly signed receipt carrying the SAME
    # nonce still works. If step 6 had run first this would 403 as a replay.
    good = _resign(keys, _mint(keys, now=clock()), nonce=receipt.nonce)
    good = _resign(keys, good,
                   bound_params=compute_bound_params(receipt.nonce,
                                                     {"customer_id": "c-1"}))
    r = client.delete("/customers/c-1", headers=_hdr(good))
    assert r.json() == {"deleted": "c-1"}, "the forgery must not burn the nonce"


def test_expired_receipt_burns_no_nonce_and_makes_no_network_call():
    """Ordering: a step-2 failure must not reach step 5 or step 6."""
    client, clock, status, keys, customers = _setup()
    receipt = _mint(keys)
    clock.advance(31)

    assert client.delete("/customers/c-1",
                         headers=_hdr(receipt)).status_code == 403
    assert status.calls == 0, "expiry is checked before the revocation fetch"

    good = _resign(keys, _mint(keys, now=clock()), nonce=receipt.nonce)
    good = _resign(keys, good,
                   bound_params=compute_bound_params(receipt.nonce,
                                                     {"customer_id": "c-1"}))
    assert client.delete("/customers/c-1",
                         headers=_hdr(good)).json() == {"deleted": "c-1"}


# --------------------------------------------------------------------------- #
# Action mismatch.
# --------------------------------------------------------------------------- #


def test_read_receipt_replayed_against_delete():
    """Attack: a receipt legitimately issued for ``read_customer`` — same
    passport, same schema, unexpired, unused — presented at the delete."""
    client, clock, status, keys, customers = _setup()
    receipt = _mint(keys, tool="read_customer", action_class="read",
                    bound_values={"customer_id": "c-1"})

    r = client.delete("/customers/c-1", headers=_hdr(receipt))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt does not authorise this call"
    assert customers["c-1"] == "Ada Lovelace"

    # Control: that same receipt is valid where it was issued for.
    receipt2 = _mint(keys, tool="read_customer", action_class="read")
    assert client.get("/customers/c-1",
                      headers=_hdr(receipt2)).status_code == 200


def test_same_tool_name_different_params_schema():
    """Attack: right tool, right class, right instance — schema drifted.

    Fails action-match closed, which is the intended (operationally noisy)
    behaviour rather than a silent risk.
    """
    client, clock, status, keys, customers = _setup()
    receipt = _mint(keys, params_schema=OTHER_SCHEMA)

    r = client.delete("/customers/c-1", headers=_hdr(receipt))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt does not authorise this call"
    assert customers["c-1"] == "Ada Lovelace"


def test_action_class_downgraded_to_write_at_issuance():
    """Attack: get the gateway to mint the *same* tool and *same* instance as
    ``write`` instead of ``irreversible``, to duck the live revocation check
    (step 5) and the bound-params check (step 4).

    ``action_class`` is authenticated inside ``action_hash``
    (receipt.py:122), and the endpoint hashes with *its own* class — never the
    token's (middleware.py:215) — so the downgrade cannot even match the shape.
    """
    client, clock, status, keys, customers = _setup()
    status.status = "revoked"  # the check the downgrade is trying to skip
    receipt = _mint(keys, action_class="write",
                    bound_values={"customer_id": "c-1"})

    r = client.delete("/customers/c-1", headers=_hdr(receipt))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt does not authorise this call"
    assert customers["c-1"] == "Ada Lovelace"
    assert status.calls == 0, "rejected at step 3, before the revocation check"


def test_action_class_field_mutated_after_signing():
    """Attack: flip ``action_class`` irreversible→write in the wire bytes and
    drop the instance binding with it, so step 4 has nothing to compare."""
    client, clock, status, keys, customers = _setup()
    raw = _mint(keys).model_dump(mode="json")
    raw["action_class"] = "write"
    raw["bound_params"] = None
    raw["bound_param_names"] = None

    r = client.delete("/customers/c-1", headers=_hdr(raw))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert customers["c-1"] == "Ada Lovelace"
    assert status.calls == 0


def test_action_hash_swapped_for_the_delete_hash():
    """Attack: keep a read receipt's envelope, paste in the delete endpoint's
    own ``action_hash``. Only works if action_hash is outside the signature."""
    client, clock, status, keys, customers = _setup()
    target = _mint(keys)  # a real delete receipt, for its hash only
    raw = _mint(keys, tool="read_customer", action_class="read",
                bound_values={"customer_id": "c-1"}).model_dump(mode="json")
    raw["action_hash"] = target.action_hash
    raw["action_class"] = "irreversible"
    raw["bound_params"] = compute_bound_params(raw["nonce"],
                                               {"customer_id": "c-1"})
    raw["bound_param_names"] = ["customer_id"]

    r = client.delete("/customers/c-1", headers=_hdr(raw))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert customers["c-1"] == "Ada Lovelace"
