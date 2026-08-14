"""The cross-worker replay: one receipt, two workers, one deletion.

``InMemoryNonceStore`` cannot survive this — worker B's dict has never seen
worker A's nonce — which is why it is no longer the default. These tests pin
the default's actual guarantee: the claim is arbitrated outside the process,
so a second worker on the same host loses the race and returns 403.

The deletion is observed, not inferred: ``executions`` records every call that
got through, because a 403 arriving after the row is already gone is exactly
the failure being tested for.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agenttic.config import load_config
from agenttic.gate.middleware import (
    HEADER_NAME,
    FileNonceStore,
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


def _worker(keys, nonce_store, executions):
    """One worker process's view of the same tool: its own store, shared state."""
    app = FastAPI()

    @app.delete("/customers/{customer_id}")
    @require_receipt("delete_customer", "irreversible", CUSTOMER_ID_SCHEMA,
                     ["customer_id"], jwks=keys.jwks, nonce_store=nonce_store,
                     revocations=RevocationCache(
                         fetcher=lambda url: {"status": "active"}, now=lambda: T0),
                     now=T0)
    def delete_customer(customer_id: str):
        executions.append(customer_id)
        return {"deleted": customer_id}

    return TestClient(app)


def _headers(keys):
    receipt = issue_tool_access_receipt(
        keys, tool="delete_customer", action_class="irreversible",
        params_schema=CUSTOMER_ID_SCHEMA, bound_values={"customer_id": "c-1"},
        passport_id="pp-workers", passport_hash="a1c9",
        principal=Principal(id="sub:okta|jane.doe", via=["agent:evil-bot"]),
        gateway_id="gw:test", decision_id="decision:test", policy_hash="e0aa",
        now=T0)
    return {HEADER_NAME: encode_receipt_header(receipt.model_dump(mode="json"))}


def test_replay_across_workers_deletes_once(tmp_path):
    keys = PassportKeyManager(CFG, private_key=generate_key())
    executions: list[str] = []
    # Two stores, as two worker processes would have — the shared directory is
    # the only thing they have in common, and it is enough.
    # Both stores run on the SAME fake clock the receipts are minted against.
    # Without that they prune on wall time, so A's claim — stamped T0+60s — is
    # already expired the moment the calendar passes T0, and B's prune deletes it
    # before B claims. The test then reads as "replay allowed" when what actually
    # happened is that the claim was garbage-collected. It passed on the day it
    # was written and failed the next.
    a = _worker(keys, FileNonceStore(str(tmp_path / "nonces"), now=lambda: T0),
                executions)
    b = _worker(keys, FileNonceStore(str(tmp_path / "nonces"), now=lambda: T0),
                executions)

    headers = _headers(keys)
    assert a.delete("/customers/c-1", headers=headers).status_code == 200
    assert b.delete("/customers/c-1", headers=headers).status_code == 403
    assert executions == ["c-1"], "the irreversible action ran on both workers"


def test_in_memory_store_is_why_it_is_not_the_default(tmp_path):
    """The positive control. Without it, the test above only proves the
    filesystem exists, not that the store is what stopped the second call."""
    keys = PassportKeyManager(CFG, private_key=generate_key())
    executions: list[str] = []
    a = _worker(keys, InMemoryNonceStore(now=lambda: T0), executions)
    b = _worker(keys, InMemoryNonceStore(now=lambda: T0), executions)

    headers = _headers(keys)
    a.delete("/customers/c-1", headers=headers)
    b.delete("/customers/c-1", headers=headers)
    assert executions == ["c-1", "c-1"]  # the bypass the default now closes


def test_expired_nonces_are_pruned(tmp_path):
    """Retention stays bounded by TTL: a claim past expiry is forgotten, and a
    replay of it is rejected at step 2 (expiry) rather than step 6 anyway."""
    directory = tmp_path / "nonces"
    clock = [T0]
    store = FileNonceStore(str(directory), now=lambda: clock[0])

    assert store.claim("n-1", T0 + timedelta(seconds=30)) is True
    assert store.claim("n-1", T0 + timedelta(seconds=30)) is False
    clock[0] = T0 + timedelta(seconds=31)
    store.claim("n-2", T0 + timedelta(seconds=60))  # any claim prunes
    assert len(list(directory.iterdir())) == 1
