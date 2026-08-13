"""Adversary slice 1 — no receipt, signature forgery, cross-protocol confusion.

Every test here is an *attempt to execute the protected action without a valid
receipt*. The tool keeps real state (a ``customers`` dict and a ``reads`` log),
so "blocked" means the side effect is observably absent, not merely that some
status code was 403. A 403 with the row already deleted is precisely the failure
this gate exists to prevent, so the side effect is asserted every time.

Three fences are exercised:

* step 0 — ``typ`` must equal ``agenttic/tool-access-receipt@1``, which is what
  stops an audit ``Receipt`` or a ``Passport`` being replayed here;
* step 1 — the Ed25519 signature covers *every* field except ``signature``, so
  any mutation, addition or deletion after signing invalidates it;
* ``ToolAccessReceipt.model_validate`` — the fence behind the signature, which
  is what would stop a structurally-wrong payload even if the signing key itself
  were used to sign it.

The status fetcher counts its calls and the nonce is reused deliberately, so the
tests also observe that a rejection at step 0/1 costs neither a network
round-trip nor the receipt's single use.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenttic.gate.middleware import (
    HEADER_NAME,
    InMemoryNonceStore,
    RevocationCache,
    encode_receipt_header,
    require_receipt,
)
from agenttic.gate.receipt import TYP, Principal, issue_tool_access_receipt
from agenttic.passport.keys import (
    PassportKeyManager,
    generate_key,
    key_id,
    sign_payload,
)
from agenttic.schema.passport import Passport, PassportClaims, Receipt
from agenttic.verifier.header import encode_passport_header
from examples.receipt_gated_tool import CUSTOMER_ID_SCHEMA

T0 = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
PASSPORT = "pp-attack-1"

# Overlap window deliberately in the past: rotation drops the old key from the
# JWKS immediately, so "retired key" means retired, not "still published".
NO_OVERLAP_CFG = {"passport": {"key_rotation_overlap_days": -1}}


class _Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class _Status:
    """Counts calls, so "step 1 rejected it before the network" is observed."""

    def __init__(self) -> None:
        self.calls = 0
        self.status = "active"

    def __call__(self, url: str) -> dict:
        self.calls += 1
        return {"status": self.status}


class _Tool:
    """The gated tool plus the state a successful call would actually change."""

    def __init__(self, cfg: dict | None = None) -> None:
        self.clock, self.status = _Clock(), _Status()
        self.keys = PassportKeyManager(cfg or {}, private_key=generate_key())
        self.customers = {"c-1": "Ada Lovelace", "c-2": "Grace Hopper"}
        self.reads: list[str] = []
        gate = dict(jwks=self.keys.jwks,
                    nonce_store=InMemoryNonceStore(now=self.clock),
                    revocations=RevocationCache(fetcher=self.status,
                                                now=self.clock),
                    now=self.clock)

        app = FastAPI()

        @app.get("/customers/{customer_id}")
        @require_receipt("read_customer", "read", CUSTOMER_ID_SCHEMA, **gate)
        def read_customer(customer_id: str):
            self.reads.append(customer_id)  # the observable side effect
            return {"customer_id": customer_id,
                    "name": self.customers[customer_id]}

        @app.delete("/customers/{customer_id}")
        @require_receipt("delete_customer", "irreversible", CUSTOMER_ID_SCHEMA,
                         ["customer_id"], **gate)
        def delete_customer(customer_id: str):
            del self.customers[customer_id]  # unrecoverable side effect
            return {"deleted": customer_id}

        self.client = TestClient(app)

    # -- receipt construction ------------------------------------------- #

    def receipt(self, tool="delete_customer", action_class="irreversible",
                bound_values=None, **kw) -> dict:
        if action_class == "irreversible" and bound_values is None:
            bound_values = {"customer_id": "c-1"}
        r = issue_tool_access_receipt(
            self.keys, tool=tool, action_class=action_class,
            params_schema=CUSTOMER_ID_SCHEMA, bound_values=bound_values,
            passport_id=PASSPORT, passport_hash="a1c9",
            principal=Principal(id="sub:okta|jane.doe",
                                via=["agent:triage-bot"]),
            gateway_id="gw:test", decision_id="decision:test",
            policy_hash="e0aa", now=self.clock(), **kw)
        return r.model_dump(mode="json")

    # -- attack transport ------------------------------------------------ #

    def delete(self, header_value: str | None, customer_id: str = "c-1"):
        headers = {} if header_value is None else {HEADER_NAME: header_value}
        return self.client.delete(f"/customers/{customer_id}", headers=headers)

    def get(self, header_value: str | None, customer_id: str = "c-1"):
        headers = {} if header_value is None else {HEADER_NAME: header_value}
        return self.client.get(f"/customers/{customer_id}", headers=headers)

    def untouched(self) -> bool:
        """No side effect happened on either endpoint."""
        return (self.customers == {"c-1": "Ada Lovelace", "c-2": "Grace Hopper"}
                and self.reads == [])


def _hdr(payload: dict) -> str:
    return encode_receipt_header(payload)


def _resign(payload: dict, priv) -> dict:
    """Re-sign a mutated payload with an arbitrary key — the attacker's key, a
    retired key, whatever. Exactly the bytes the verifier will re-canonicalize."""
    out = dict(payload)
    out["signature"] = sign_payload(
        priv, {k: v for k, v in out.items() if k != "signature"})
    return out


# ======================================================================= #
# 1. No receipt at all.
# ======================================================================= #


def test_attack_no_header_at_all():
    t = _Tool()
    r = t.delete(None)
    assert r.status_code == 403
    assert r.json()["detail"] == "missing tool access receipt"
    assert t.untouched()


def test_attack_empty_header_value():
    t = _Tool()
    # `if not header` treats "" as absent — an empty string never reaches the
    # decoder, so there is no "" -> {} coercion to exploit.
    r = t.delete("")
    assert r.status_code == 403
    assert r.json()["detail"] == "missing tool access receipt"
    assert t.untouched()
    assert t.status.calls == 0


def test_attack_whitespace_header_is_not_an_empty_receipt():
    t = _Tool()
    r = t.delete("   ")
    assert r.status_code == 403
    assert t.untouched()


def test_attack_malformed_base64_header():
    t = _Tool()
    for junk in ("!!!not-base64!!!", "%%%%", "ZZZ", "a"):
        r = t.delete(junk)
        assert r.status_code == 403, junk
        assert r.json()["detail"] == "tool access receipt rejected"
    assert t.untouched()


def test_attack_valid_base64_that_is_not_json():
    t = _Tool()
    r = t.delete(base64.b64encode(b"\x00\x01\x02 not json").decode())
    assert r.status_code == 403
    assert t.untouched()


def test_attack_valid_json_that_is_not_an_object():
    t = _Tool()
    # step 0 tests isinstance(raw, dict) BEFORE .get("typ") — a list/str/null
    # cannot reach an AttributeError path, and none of them execute anything.
    for doc in ([], [{"typ": TYP}], "agenttic/tool-access-receipt@1", None,
                1234, True):
        r = t.delete(base64.b64encode(json.dumps(doc).encode()).decode())
        assert r.status_code == 403, doc
        assert r.json()["detail"] == "tool access receipt rejected", doc
    assert t.untouched()
    assert t.status.calls == 0


def test_attack_empty_object_and_typ_only_object():
    t = _Tool()
    for doc in ({}, {"typ": TYP}, {"typ": TYP, "signature": ""},
                {"typ": "agenttic/tool-access-receipt@2"}):
        r = t.delete(_hdr(doc))
        assert r.status_code == 403, doc
        assert t.untouched(), doc


def test_attack_unsigned_but_otherwise_perfect_receipt():
    """Every field right, signature blank. Step 1 is not optional."""
    t = _Tool()
    d = t.receipt()
    d["signature"] = ""
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.untouched()


def test_attack_signature_key_deleted_entirely():
    t = _Tool()
    d = t.receipt()
    del d["signature"]
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.untouched()


# ======================================================================= #
# 2. Signature attacks.
# ======================================================================= #


def test_attack_flipped_byte_in_signature():
    t = _Tool()
    d = t.receipt()
    raw = bytearray(base64.b64decode(d["signature"]))
    raw[0] ^= 0x01  # flip one bit of the real Ed25519 signature
    d["signature"] = base64.b64encode(bytes(raw)).decode()
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.untouched()
    assert t.status.calls == 0, "rejected offline, before any network call"


def test_attack_expiry_extended_after_signing():
    """The classic: take a real receipt, push expires_at out. It must die at
    step 1 (signature), never reaching step 2, because timestamps are signed."""
    t = _Tool()
    d = t.receipt()
    d["expires_at"] = "2099-01-01T00:00:00+00:00"
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.untouched()


def test_attack_nonce_swapped_after_signing():
    """A fresh nonce on a spent receipt would defeat single-use. It cannot be
    swapped: the nonce is inside the signed payload."""
    t = _Tool()
    d = t.receipt()
    good = _hdr(d)
    assert t.delete(good).json() == {"deleted": "c-1"}  # positive control

    d["nonce"] = "AAAAAAAAAAAAAAAAAAAAAA"
    r = t.delete(_hdr(d), "c-2")
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.customers == {"c-2": "Grace Hopper"}  # c-1 gone legitimately only


def test_attack_action_class_downgraded_after_signing():
    """irreversible -> write would skip both the live revocation check and the
    bound-params check. action_class is signed, and it is also inside
    action_hash, so the downgrade is doubly dead."""
    t = _Tool()
    d = t.receipt()
    d["action_class"] = "write"
    d["bound_params"] = None
    d["bound_param_names"] = None
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.untouched()


def test_attack_bound_params_stripped_after_signing():
    t = _Tool()
    d = t.receipt()
    del d["bound_params"]
    del d["bound_param_names"]
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert t.untouched()


def test_attack_extra_field_injected_into_signed_payload():
    """Additive tampering, not just mutation: the verifier canonicalizes the
    dict it was handed, so an added key changes the signed bytes."""
    t = _Tool()
    d = t.receipt()
    d["admin"] = True
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.untouched()


def test_attack_optional_null_field_removed_after_signing():
    """not_before is serialized as a concrete value; dropping the key is a
    payload change even though pydantic would happily default it back."""
    t = _Tool()
    d = t.receipt("read_customer", "read")
    del d["not_before"]
    r = t.get(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.untouched()


def test_attack_signed_by_a_foreign_key_with_its_own_kid():
    """A whole receipt minted by an attacker's Ed25519 key. Self-consistent and
    perfectly signed — the kid is simply not in the tool's JWKS."""
    t = _Tool()
    evil = generate_key()
    d = t.receipt()
    d["key_id"] = key_id(evil.public_key())
    d = _resign(d, evil)
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signed by an unknown key"
    assert t.untouched()
    assert t.status.calls == 0


def test_attack_signed_by_a_foreign_key_claiming_the_real_kid():
    """kid points at a key that IS in the JWKS; the signature is by another.
    The kid is a lookup hint, not evidence."""
    t = _Tool()
    d = _resign(t.receipt(), generate_key())  # key_id left as the real one
    assert d["key_id"] == t.keys.key_id()
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.untouched()


def test_attack_unknown_kid():
    t = _Tool()
    for kid in ("deadbeefdeadbeef", "", None):
        d = t.receipt()
        d["key_id"] = kid
        r = t.delete(_hdr(d))
        assert r.status_code == 403, kid
        assert t.untouched(), kid


def test_attack_empty_jwks_rejects_a_genuine_receipt():
    """Fail closed when the tool holds no keys at all — no "nothing to check
    against, therefore fine" path."""
    t = _Tool()
    app = FastAPI()
    seen: list[str] = []

    @app.delete("/customers/{customer_id}")
    @require_receipt("delete_customer", "irreversible", CUSTOMER_ID_SCHEMA,
                     ["customer_id"], jwks={"keys": []},
                     nonce_store=InMemoryNonceStore(now=t.clock),
                     revocations=RevocationCache(fetcher=t.status, now=t.clock),
                     now=t.clock)
    def delete_customer(customer_id: str):
        seen.append(customer_id)
        return {"deleted": customer_id}

    r = TestClient(app).delete("/customers/c-1",
                               headers={HEADER_NAME: _hdr(t.receipt())})
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signed by an unknown key"
    assert seen == []


def test_attack_retired_key_signs_but_claims_the_current_kid():
    """Rotation-overlap key confusion: the pair is {retired, current}. Signing
    with the retired key while naming the current kid must not verify."""
    t = _Tool(NO_OVERLAP_CFG)
    d = t.receipt()
    retired_priv = t.keys._priv
    new_kid = t.keys.rotate()
    assert new_kid != d["key_id"]
    d["key_id"] = new_kid
    d = _resign(d, retired_priv)
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.untouched()


def test_attack_retired_key_after_the_overlap_window_closed():
    """Same retired key, honest kid. Inside the overlap window this verifies by
    design (that is what overlap is for); once the window shuts, the key is gone
    from the JWKS and the receipt is refused."""
    t = _Tool(NO_OVERLAP_CFG)
    d = t.receipt()                       # signed by the soon-to-be-old key
    old_kid = d["key_id"]
    t.keys.rotate()                       # overlap = -1 day: closed instantly
    assert old_kid not in [k["kid"] for k in t.keys.jwks()["keys"]]
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signed by an unknown key"
    assert t.untouched()


def test_rotation_overlap_is_honoured_for_the_key_that_earned_it():
    """The control on the two tests above: a receipt signed just before a
    rotation, with a live overlap window, still works. If this failed the two
    "blocked" results above would be proving nothing about kid handling."""
    t = _Tool({"passport": {"key_rotation_overlap_days": 14}})
    d = t.receipt()
    t.keys.rotate()
    assert t.delete(_hdr(d)).json() == {"deleted": "c-1"}


def test_bad_signature_burns_neither_the_nonce_nor_a_network_call():
    """Ordering, proven from the outside: present a tampered receipt, then the
    genuine one carrying the SAME nonce. If step 1 had run after step 6 the
    good receipt would now be a replay."""
    t = _Tool()
    d = t.receipt()
    tampered = dict(d, expires_at="2099-01-01T00:00:00+00:00")

    assert t.delete(_hdr(tampered)).status_code == 403
    assert t.status.calls == 0, "no revocation round-trip for a bad signature"
    assert t.customers["c-1"] == "Ada Lovelace"

    assert t.delete(_hdr(d)).json() == {"deleted": "c-1"}, \
        "the tampered attempt must not have consumed the nonce"
    assert t.status.calls == 1


# ======================================================================= #
# 3. Cross-protocol confusion.
# ======================================================================= #


def _audit_receipt(keys: PassportKeyManager) -> dict:
    r = Receipt(receipt_id="rcpt-1", passport_id=PASSPORT, agent_id="agent:x",
                tool_call_ref="tool:delete_customer", action_class="write",
                policy_hash="e0aa", decision_id="decision:test",
                key_id=keys.key_id())
    r.signature = keys.sign(r.signing_input())
    return r.model_dump(mode="json")


def _passport(keys: PassportKeyManager) -> dict:
    claims = PassportClaims(
        agent_id="agent:x", tier="t1", dossier_sha256="d1", policy_hash="e0aa",
        issued_at=T0, expires_at=T0 + timedelta(days=30),
        status_url=f"https://agenttic.local/passport/{PASSPORT}/status",
        key_id=keys.key_id())
    p = Passport(passport_id=PASSPORT, claims=claims)
    p.signature = keys.sign(p.signing_input())
    return p.model_dump(mode="json")


def test_attack_audit_receipt_presented_as_a_tool_access_receipt():
    """A real, correctly-signed audit Receipt from passport/receipts.py — same
    signing key, same JWKS, and it even names delete_customer. It has no typ,
    so step 0 refuses it before the signature is ever consulted."""
    t = _Tool()
    d = _audit_receipt(t.keys)
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt rejected"
    assert t.untouched()
    assert t.status.calls == 0


def test_attack_audit_receipt_with_typ_bolted_on():
    t = _Tool()
    d = dict(_audit_receipt(t.keys), typ=TYP)
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert t.untouched()


def test_attack_audit_receipt_with_typ_and_a_genuine_signature():
    """The worst case for step 0: the payload passes typ AND carries a real
    signature by the gateway's own key. Structural validation is the second
    fence — an audit Receipt has no action_hash, nonce or expires_at, so
    model_validate refuses it and the refusal is a 403, not a 500."""
    t = _Tool()
    d = _resign(dict(_audit_receipt(t.keys), typ=TYP), t.keys._priv)
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt rejected"
    assert t.untouched()


def test_attack_passport_presented_as_a_tool_access_receipt():
    t = _Tool()
    r = t.delete(_hdr(_passport(t.keys)))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt rejected"
    assert t.untouched()


def test_attack_passport_in_the_agent_passport_wire_format():
    """Same base64-of-JSON codec on both headers, so an Agent-Passport value
    drops straight into Agent-Tool-Receipt. The codec is shared; the typ check
    is what keeps the protocols apart."""
    t = _Tool()
    r = t.delete(encode_passport_header(_passport(t.keys)))
    assert r.status_code == 403
    assert t.untouched()


def test_attack_passport_with_typ_and_a_genuine_signature():
    t = _Tool()
    d = _resign(dict(_passport(t.keys), typ=TYP, key_id=t.keys.key_id()),
                t.keys._priv)
    r = t.delete(_hdr(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt rejected"
    assert t.untouched()


def test_attack_nested_receipt_smuggled_inside_another_envelope():
    """A valid receipt wrapped in an outer object — the shape a naive verifier
    that "finds" the token anywhere in the body would accept."""
    t = _Tool()
    for wrapper in ({"receipt": t.receipt()}, {"typ": TYP, "receipt": t.receipt()},
                    {"data": {"typ": TYP}}):
        r = t.delete(_hdr(wrapper))
        assert r.status_code == 403, wrapper
        assert t.untouched(), wrapper


def test_positive_control_a_genuine_receipt_still_executes():
    """Without this every assertion above is satisfiable by an endpoint that
    never works at all."""
    t = _Tool()
    assert t.delete(_hdr(t.receipt())).json() == {"deleted": "c-1"}
    assert t.customers == {"c-2": "Grace Hopper"}
    assert t.get(_hdr(t.receipt("read_customer", "read")), "c-2").status_code == 200
    assert t.reads == ["c-2"]
