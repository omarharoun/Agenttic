"""Adversarial: instance substitution and replay (sequential + concurrent).

Every test here tries to make ``delete_customer`` actually delete something it
was never authorised to delete. The endpoint therefore mutates real state and
appends to an ``executions`` list: a 403 with the row already gone is the exact
failure the gate exists to prevent, so execution has to be observable and not
inferred from the status code.

Three slices:
  * substitution — a receipt minted for ``c-1`` aimed at ``c-2`` (§4), including
    the two forgeries an attacker reaches for next (re-hash ``bound_params``,
    re-salt with a fresh nonce).
  * replay, sequential — same receipt twice, plus the two ways to dodge a naive
    dedup (fresh nonce, byte-different header).
  * replay, concurrent — N threads, one receipt. ``test_check_then_insert_...``
    is the positive control: a deliberately split store, defined *here* and
    never in production, that the same harness does break. Without it "exactly
    one succeeded" only proves the threads didn't overlap.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
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
    verify_tool_receipt,
)
from agenttic.gate.receipt import (
    Principal,
    compute_bound_params,
    issue_tool_access_receipt,
    new_nonce,
)
from agenttic.passport.keys import PassportKeyManager, generate_key
from examples.receipt_gated_tool import CUSTOMER_ID_SCHEMA

CFG = load_config("config.yaml")
T0 = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
PASSPORT = "pp-attack-3"


class _Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class _Tool:
    """The victim: real state, and a log of every execution that got through."""

    def __init__(self, *, keys=None, nonce_store=None, customers=None,
                 executions=None):
        self.clock = _Clock()
        self.keys = keys or PassportKeyManager(CFG, private_key=generate_key())
        self.customers = customers if customers is not None else {
            "c-1": "Ada Lovelace", "c-2": "Grace Hopper"}
        self.executions = executions if executions is not None else []
        self.nonce_store = nonce_store or InMemoryNonceStore(now=self.clock)
        self.revocations = RevocationCache(
            fetcher=lambda url: {"status": "active"}, now=self.clock)
        gate = dict(jwks=self.keys.jwks, nonce_store=self.nonce_store,
                    revocations=self.revocations, now=self.clock)

        app = FastAPI()

        @app.delete("/customers/{customer_id}")
        @require_receipt("delete_customer", "irreversible", CUSTOMER_ID_SCHEMA,
                         ["customer_id"], **gate)
        def delete_customer(customer_id: str):
            # pop, not del: a second execution must show up in `executions`
            # rather than as a KeyError 500, which would look like a block.
            self.executions.append(customer_id)
            self.customers.pop(customer_id, None)
            return {"deleted": customer_id}

        self.app = app
        self.client = TestClient(app)

    def receipt(self, customer_id: str):
        return issue_tool_access_receipt(
            self.keys, tool="delete_customer", action_class="irreversible",
            params_schema=CUSTOMER_ID_SCHEMA,
            bound_values={"customer_id": customer_id},
            passport_id=PASSPORT, passport_hash="a1c9",
            principal=Principal(id="sub:okta|jane.doe", via=["agent:evil-bot"]),
            gateway_id="gw:test", decision_id="decision:test",
            policy_hash="e0aa", now=self.clock())

    def headers(self, raw: dict) -> dict:
        return {HEADER_NAME: encode_receipt_header(raw)}


def _raw(receipt) -> dict:
    return receipt.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# Instance substitution — receipt for delete_customer(c-1), fired at c-2.
# --------------------------------------------------------------------------- #


def test_substitution_receipt_for_c1_cannot_delete_c2():
    tool = _Tool()
    headers = tool.headers(_raw(tool.receipt("c-1")))

    r = tool.client.delete("/customers/c-2", headers=headers)

    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt does not authorise this call"
    assert tool.executions == [], "the substituted delete must not have run"
    assert tool.customers["c-2"] == "Grace Hopper"

    # ...and the failed substitution did not burn the nonce (step 4 is before
    # step 6). If it had, substitution would be a free denial-of-service on the
    # legitimate call the receipt was actually minted for.
    r = tool.client.delete("/customers/c-1", headers=headers)
    assert r.json() == {"deleted": "c-1"}
    assert tool.executions == ["c-1"]


def test_substitution_with_rehashed_bound_params_fails_signature():
    """The obvious next move: recompute bound_params for c-2 under the same
    nonce and splice it in. bound_params is inside the signed payload."""
    tool = _Tool()
    raw = _raw(tool.receipt("c-1"))
    raw["bound_params"] = compute_bound_params(raw["nonce"], {"customer_id": "c-2"})

    r = tool.client.delete("/customers/c-2", headers=tool.headers(raw))

    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert tool.executions == []
    assert tool.customers["c-2"] == "Grace Hopper"


def test_substitution_with_fresh_nonce_and_matching_salt_fails_signature():
    """Full forgery: new nonce (dodges the replay store) plus bound_params
    re-salted under it so step 4 would pass. Both fields are signed."""
    tool = _Tool()
    raw = _raw(tool.receipt("c-1"))
    raw["nonce"] = new_nonce()
    raw["bound_params"] = compute_bound_params(raw["nonce"], {"customer_id": "c-2"})

    r = tool.client.delete("/customers/c-2", headers=tool.headers(raw))

    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt signature invalid"
    assert tool.executions == []
    assert tool.customers == {"c-1": "Ada Lovelace", "c-2": "Grace Hopper"}


def test_substitution_by_widening_bound_param_names_is_rejected():
    """Claim the receipt binds nothing the tool asks about, hoping the name
    comparison in step 4 is skipped when the lists disagree."""
    tool = _Tool()
    raw = _raw(tool.receipt("c-1"))
    raw["bound_param_names"] = []
    raw["bound_params"] = None

    r = tool.client.delete("/customers/c-2", headers=tool.headers(raw))

    assert r.status_code == 403
    assert tool.executions == []
    assert tool.customers["c-2"] == "Grace Hopper"


# --------------------------------------------------------------------------- #
# Replay — sequential.
# --------------------------------------------------------------------------- #


def test_sequential_replay_blocked():
    tool = _Tool()
    headers = tool.headers(_raw(tool.receipt("c-1")))

    assert tool.client.delete("/customers/c-1", headers=headers).status_code == 200
    tool.customers["c-1"] = "Ada Lovelace"  # restore, so a replay is visible

    r = tool.client.delete("/customers/c-1", headers=headers)

    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt already used"
    assert tool.executions == ["c-1"], "executed exactly once"
    assert tool.customers["c-1"] == "Ada Lovelace"


def test_replay_with_reencoded_header_blocked():
    """Byte-different header, same receipt: re-serialise the JSON with a
    different key order. If the replay defence keyed on the header bytes (or a
    receipt_id/body digest) instead of the nonce, this would sail through."""
    tool = _Tool()
    raw = _raw(tool.receipt("c-1"))
    assert tool.client.delete("/customers/c-1",
                              headers=tool.headers(raw)).status_code == 200
    tool.customers["c-1"] = "Ada Lovelace"

    shuffled = dict(reversed(list(raw.items())))
    assert json.dumps(shuffled) != json.dumps(raw)  # different bytes...
    assert shuffled == raw                          # ...same receipt

    r = tool.client.delete("/customers/c-1", headers=tool.headers(shuffled))

    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt already used"
    assert tool.executions == ["c-1"]


def test_replay_after_the_nonce_store_prunes_it_is_still_blocked():
    """The store forgets a nonce once it is past expires_at. That is only safe
    because step 2 rejects the expired receipt first — check that ordering, not
    the comment about it."""
    tool = _Tool()
    receipt = tool.receipt("c-1")
    headers = tool.headers(_raw(receipt))
    assert tool.client.delete("/customers/c-1", headers=headers).status_code == 200
    tool.customers["c-1"] = "Ada Lovelace"
    assert receipt.nonce in tool.nonce_store._seen

    tool.clock.advance(31)  # past the 30s irreversible TTL
    tool.nonce_store.claim("unrelated", tool.clock() + timedelta(seconds=30))
    assert receipt.nonce not in tool.nonce_store._seen, "pruned — step 6 forgot it"

    r = tool.client.delete("/customers/c-1", headers=headers)

    assert r.status_code == 403
    assert r.json()["detail"] == "tool access receipt expired"
    assert tool.executions == ["c-1"]
    assert tool.customers["c-1"] == "Ada Lovelace"


# --------------------------------------------------------------------------- #
# Replay — concurrent (the TOCTOU window).
# --------------------------------------------------------------------------- #


def test_concurrent_replay_over_http_exactly_one_succeeds():
    tool = _Tool()
    headers = tool.headers(_raw(tool.receipt("c-1")))
    n = 24
    start = threading.Barrier(n)

    def fire(_):
        start.wait()
        r = tool.client.delete("/customers/c-1", headers=headers)
        return r.status_code, r.json().get("detail")

    with ThreadPoolExecutor(max_workers=n) as pool:
        out = list(pool.map(fire, range(n)))

    assert out.count((200, None)) == 1, out
    # every loser lost on the nonce, not on some earlier accident — all 24 got
    # through signature, expiry, action shape, binding and revocation together.
    assert out.count((403, "tool access receipt already used")) == n - 1, out
    assert tool.executions == ["c-1"], f"side effect ran {len(tool.executions)}x"


def _hammer(claim, rounds=25, threads=32):
    """Run `claim` from `threads` threads released by a barrier, `rounds` times.
    Returns the per-round count of threads that got through."""
    wins = []
    for _ in range(rounds):
        gate = threading.Barrier(threads)
        won = []
        lock = threading.Lock()

        def go(_):
            gate.wait()
            if claim():
                with lock:
                    won.append(1)

        with ThreadPoolExecutor(max_workers=threads) as pool:
            list(pool.map(go, range(threads)))
        wins.append(len(won))
    return wins


def test_concurrent_verify_claims_the_nonce_exactly_once():
    """Straight at the pipeline, no event loop in the way: the HTTP test above
    is serialised by FastAPI's loop, so it cannot see a store-level race even if
    one existed. This one can."""
    tool = _Tool()
    wins = []
    for _ in range(25):
        receipt = tool.receipt("c-1")

        def claim():
            try:
                verify_tool_receipt(
                    receipt, tool.keys.jwks(), tool="delete_customer",
                    action_class="irreversible", params_schema=CUSTOMER_ID_SCHEMA,
                    nonce_store=tool.nonce_store, revocations=tool.revocations,
                    bound_values={"customer_id": "c-1"}, now=tool.clock)
                return True
            except Exception:
                return False

        wins += _hammer(claim, rounds=1, threads=32)

    assert wins == [1] * 25, f"a receipt verified more than once: {wins}"


class _CheckThenInsertStore:
    """NOT production code. The store the contract forbids (context.md: "claim-
    by-insert, never check-then-insert"), so the harness above has something it
    is known to catch."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def claim(self, nonce: str, expires_at: datetime) -> bool:
        seen = nonce in self._seen        # T-O-C
        threading.Event().wait(0.002)     # ...the window
        if seen:
            return False
        self._seen.add(nonce)             # T-O-U
        return True


def test_check_then_insert_store_loses_the_same_race():
    """Positive control. If this passes and the real store still claims once,
    the concurrency test is measuring something."""
    broken = _CheckThenInsertStore()
    nonce = new_nonce()
    wins = _hammer(lambda: broken.claim(nonce, T0 + timedelta(seconds=30)),
                   rounds=1, threads=32)
    assert wins[0] > 1, "the harness cannot detect a TOCTOU; the test above is vacuous"


def test_concurrent_substitution_and_replay_mixed():
    """N threads, one receipt bound to c-1, half of them aimed at c-2. At most
    one execution total, and it must be the authorised instance."""
    tool = _Tool()
    headers = tool.headers(_raw(tool.receipt("c-1")))
    n = 24
    start = threading.Barrier(n)

    def fire(i):
        target = "c-1" if i % 2 == 0 else "c-2"
        start.wait()
        return tool.client.delete(f"/customers/{target}", headers=headers).status_code

    with ThreadPoolExecutor(max_workers=n) as pool:
        codes = list(pool.map(fire, range(n)))

    assert codes.count(200) == 1, codes
    assert tool.executions == ["c-1"]
    assert tool.customers["c-2"] == "Grace Hopper"


def test_replay_across_two_workers_with_separate_nonce_stores():
    """The documented ceiling, executed rather than described (middleware.py
    83-89: "two workers each hold their own dict, so a replay across workers
    wins"). Two processes of the same tool, one shared database, one receipt.

    This is a deployment property, not a defeated check: the fix is the
    registry-backed store the ponytail comment names. It is here so the cost of
    the default is a measured fact and not a footnote.
    """
    customers = {"c-1": "Ada Lovelace"}
    executions: list[str] = []
    worker_a = _Tool(customers=customers, executions=executions)
    # same gateway signing key on both workers; only the nonce store differs
    worker_b = _Tool(keys=worker_a.keys, customers=customers,
                     executions=executions)
    assert worker_a.nonce_store is not worker_b.nonce_store

    headers = worker_a.headers(_raw(worker_a.receipt("c-1")))
    a = worker_a.client.delete("/customers/c-1", headers=headers)
    customers["c-1"] = "Ada Lovelace"  # restore, so a second delete is visible
    b = worker_b.client.delete("/customers/c-1", headers=headers)

    assert a.status_code == 200
    assert b.status_code == 200, "known ceiling: worker B never saw the nonce"
    assert executions == ["c-1", "c-1"], "one receipt, two irreversible deletes"
