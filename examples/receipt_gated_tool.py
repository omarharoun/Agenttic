"""A third-party tool that refuses any call not carrying a Tool Access Receipt.

The whole loop, end to end::

    keys = PassportKeyManager(...)                  # the gateway's signing key
    app = build_demo_app(keys, status_fetcher=...)  # the tool, in its own process

    receipt = issue_tool_access_receipt(            # gateway, on a Lane 1-3 allow
        keys, tool="delete_customer", action_class="irreversible",
        params_schema=CUSTOMER_ID_SCHEMA, bound_values={"customer_id": "c-1"},
        passport_id=..., passport_hash=..., principal=Principal(id="sub:jane"),
        gateway_id=..., decision_id=..., policy_hash=...)

    client.delete("/customers/c-1", headers={           # agent presents it
        HEADER_NAME: encode_receipt_header(receipt.model_dump(mode="json"))})
    # -> 200 {"deleted": "c-1"}

    client.delete("/customers/c-1")                     # no receipt
    # -> 403 missing tool access receipt

Every other way to get it wrong is also a 403: the same receipt a second time
(replay), the same receipt against ``c-2`` (substitution), 30s later (expired),
a receipt for ``read_customer`` (wrong action shape), or a revoked passport.

Nothing here talks to Agenttic at request time. The tool holds a JWKS it fetched
once plus its own declared parameter schema, and that is enough for verification
steps 0-4. Only the revocation check reaches the network — cached for ``read``,
live for ``irreversible``, which is why both endpoints exist below.

Run: ``python -m examples.receipt_gated_tool`` for the loop above as asserts.
This is a standalone app built by a factory — the production server deliberately
has no ``delete_customer`` route, fake or otherwise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from fastapi import FastAPI

from agenttic.gate.middleware import (
    DEFAULT_NONCE_STORE,
    HEADER_NAME,
    NonceStore,
    RevocationCache,
    encode_receipt_header,
    require_receipt,
)
from agenttic.gate.receipt import Principal, issue_tool_access_receipt
from agenttic.passport.keys import PassportKeyManager

# The tool's declared parameter schema. The *same* declaration is registered
# with the gateway at onboarding: both sides hash it, and drift fails the
# action match closed. Shared by both tools on purpose — the tool NAME is inside
# action_hash, so a read_customer receipt still cannot execute a delete.
CUSTOMER_ID_SCHEMA = {
    "type": "object",
    "properties": {"customer_id": {"type": "string"}},
    "required": ["customer_id"],
}


def build_demo_app(
    keys: PassportKeyManager,
    *,
    now: Callable[[], datetime] | datetime | None = None,
    status_fetcher: Callable[[str], dict] | None = None,
    nonce_store: NonceStore | None = None,
) -> FastAPI:
    """The tool, with its clock, revocation source and nonce store injected.

    A real deployment passes none of them: system clock, real HTTP status
    fetches, and the shared host-wide nonce store — NOT a per-app one, or two
    workers of this same tool each claim the nonce and delete the customer
    twice. They are parameters because revocation and expiry are time- and
    network-shaped, and a test cannot drive either one from the outside.
    """
    gate = dict(
        jwks=keys.jwks,  # callable: rotation-with-overlap stays live
        nonce_store=nonce_store or DEFAULT_NONCE_STORE,
        revocations=RevocationCache(fetcher=status_fetcher, now=now),
        now=now,
    )

    app = FastAPI(title="Example receipt-gated tool")

    @app.get("/customers/{customer_id}")
    @require_receipt("read_customer", "read", CUSTOMER_ID_SCHEMA, **gate)
    def read_customer(customer_id: str):
        # Reversible: schema-bound only, and revocation is read from the cache.
        return {"customer_id": customer_id, "name": "Ada Lovelace"}

    @app.delete("/customers/{customer_id}")
    @require_receipt("delete_customer", "irreversible", CUSTOMER_ID_SCHEMA,
                     ["customer_id"], **gate)
    def delete_customer(customer_id: str):
        # Irreversible: bound to this customer_id, and revocation is checked
        # live — a deletion is not something to do on a 60s-stale status.
        return {"deleted": customer_id}

    return app


def _demo() -> None:
    from fastapi.testclient import TestClient

    from agenttic.passport.keys import generate_key

    keys = PassportKeyManager(private_key=generate_key())
    client = TestClient(build_demo_app(
        keys, status_fetcher=lambda url: {"status": "active"}))

    assert client.delete("/customers/c-1").status_code == 403  # no receipt

    receipt = issue_tool_access_receipt(
        keys, tool="delete_customer", action_class="irreversible",
        params_schema=CUSTOMER_ID_SCHEMA, bound_values={"customer_id": "c-1"},
        passport_id="pp-demo", passport_hash="a1c9",
        principal=Principal(id="sub:okta|jane.doe", via=["agent:triage-bot"]),
        gateway_id="gw:demo", decision_id="decision:demo", policy_hash="e0aa")
    headers = {HEADER_NAME: encode_receipt_header(receipt.model_dump(mode="json"))}

    # Substitution fails at step 4 — and does NOT burn the nonce, which is the
    # whole reason the nonce claim is step 6.
    assert client.delete("/customers/c-2", headers=headers).status_code == 403
    assert client.delete("/customers/c-1", headers=headers).json() == {"deleted": "c-1"}
    assert client.delete("/customers/c-1", headers=headers).status_code == 403  # replay
    print("receipt-gated tool demo: ok")


if __name__ == "__main__":
    _demo()
