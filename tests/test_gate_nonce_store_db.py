"""The cross-HOST replay: one receipt, two hosts, one deletion.

``FileNonceStore``'s ``O_CREAT|O_EXCL`` is arbitrated by one kernel, so two
containers each get their own ``/tmp`` island and the same receipt replays once
per host. These tests pin ``DbNonceStore``'s guarantee: the claim is arbitrated
by the database's UNIQUE constraint, so a second host contending for the same
row loses and returns 403.

The harness is imported from ``test_gate_nonce_store`` rather than copied, so
the two stores are provably measured by the same instrument.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError as DBIntegrityError
from sqlmodel import Session, select

from agenttic.registry.sqlite_store import (
    PRUNE_GRACE,
    DbNonceStore,
    ToolReceiptNonceRow,
)
from tests.test_gate_nonce_store import T0, _headers, _worker
from agenttic.passport.keys import PassportKeyManager, generate_key
from agenttic.config import load_config

CFG = load_config("config.yaml")


def _store(tmp_path, *, now) -> DbNonceStore:
    """A distinct engine per call. Two of these pointed at one file is two
    hosts sharing a database — the deployment this store exists for."""
    return DbNonceStore(f"sqlite:///{tmp_path / 'nonces.db'}", now=now)


def _race(claim_a, claim_b) -> list:
    """Run two claims with both threads released at the same instant.

    The barrier is load-bearing. Without it the two calls serialise, and
    "exactly one won" would be equally true of a store with no atomicity at
    all — the assertion would pass while proving nothing.
    """
    gate = threading.Barrier(2)
    results: dict[str, object] = {}

    def run(name, claim):
        gate.wait(timeout=5)
        try:
            results[name] = claim()
        except BaseException as exc:  # a raise is not a verdict — surface it
            results[name] = exc

    threads = [threading.Thread(target=run, args=(n, c))
               for n, c in (("a", claim_a), ("b", claim_b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    verdicts = [results.get(n) for n in ("a", "b")]
    assert all(isinstance(v, bool) for v in verdicts), verdicts
    return verdicts


class _CheckThenInsertStore:
    """The positive control: the TOCTOU window ``NonceStore``'s docstring
    forbids.

    ONE shared store, not two — so a double claim cannot be blamed on separate
    state, only on the defect. Its sole difference from a correct store is that
    the membership test and the insert are two operations instead of one. The
    barrier holds that window open on purpose: under real concurrency it is
    open anyway, and forcing it makes the control deterministic rather than
    lucky.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._window = threading.Barrier(2)

    def claim(self, nonce: str, expires_at: datetime) -> bool:
        if nonce in self._seen:
            return False
        self._window.wait(timeout=5)  # both claimers are now past the check
        self._seen.add(nonce)
        return True


def test_replay_across_hosts_deletes_once(tmp_path):
    """End-to-end through the gate: two hosts, one shared database, one
    deletion. The execution is observed, not inferred — a 403 arriving after
    the row is already gone is exactly the failure being tested for."""
    keys = PassportKeyManager(CFG, private_key=generate_key())
    executions: list[str] = []
    # Both stores run on the SAME fake clock the receipts are minted against;
    # see test_expiry_uses_the_injected_clock_not_wall_time for why.
    a = _worker(keys, _store(tmp_path, now=lambda: T0), executions)
    b = _worker(keys, _store(tmp_path, now=lambda: T0), executions)

    headers = _headers(keys)
    assert a.delete("/customers/c-1", headers=headers).status_code == 200
    assert b.delete("/customers/c-1", headers=headers).status_code == 403
    assert executions == ["c-1"], "the irreversible action ran on both hosts"


def test_concurrent_claim_has_exactly_one_winner(tmp_path):
    """Two hosts claiming the SAME nonce at the same instant. The database
    arbitrates; one insert wins the constraint and the other is told it lost."""
    a = _store(tmp_path, now=lambda: T0)
    b = _store(tmp_path, now=lambda: T0)
    expires = T0 + timedelta(seconds=60)

    verdicts = _race(lambda: a.claim("n-race", expires),
                     lambda: b.claim("n-race", expires))
    assert sorted(verdicts) == [False, True], verdicts

    with Session(a.engine) as s:
        assert len(s.exec(select(ToolReceiptNonceRow)).all()) == 1


def test_check_then_insert_control_loses_the_race():
    """The positive control. Without it, the test above only proves that two
    threads ran, not that the UNIQUE constraint is what stopped the second
    claim: this store passes through the identical harness and both claimers
    win, which is the replay the real store closes."""
    store = _CheckThenInsertStore()
    expires = T0 + timedelta(seconds=60)

    verdicts = _race(lambda: store.claim("n-race", expires),
                     lambda: store.claim("n-race", expires))
    assert verdicts == [True, True], "the control was supposed to be broken"


def test_the_database_refuses_the_second_row_not_the_store(tmp_path):
    """The load-bearing primitive, pinned directly.

    ``claim`` bypassed entirely: after one claim, a second row for that nonce
    is refused by the UNIQUE constraint. This is what makes the race test above
    mean something — the guarantee is the database's, not a lucky interleaving
    of the store's control flow, and it survives any isolation level or backend
    where a prior SELECT could read stale.
    """
    store = _store(tmp_path, now=lambda: T0)
    assert store.claim("n-1", T0 + timedelta(seconds=60)) is True

    with Session(store.engine) as s:
        s.add(ToolReceiptNonceRow(
            nonce="n-1", expires_at=T0 + timedelta(seconds=60), claimed_at=T0))
        with pytest.raises(DBIntegrityError):
            s.commit()


def test_expiry_uses_the_injected_clock_not_wall_time(tmp_path):
    """T0 is in the past by wall clock. A store that pruned on wall time would
    find every claim already expired, drop it, and let the replay straight back
    in — the bug that made an earlier nonce test pass on the day it was written
    and fail the next."""
    assert T0 < datetime.now(timezone.utc), "T0 must be past for this to bite"
    store = _store(tmp_path, now=lambda: T0)
    expires = T0 + timedelta(seconds=60)

    assert store.claim("n-1", expires) is True
    assert store.claim("n-2", expires) is True  # any claim prunes first
    assert store.claim("n-1", expires) is False, \
        "n-1 was pruned on wall time; the replay window reopened"


def test_expired_nonces_are_pruned(tmp_path):
    """Retention stays bounded by TTL + PRUNE_GRACE: a claim past expiry is
    eventually forgotten, and a replay of it is rejected at step 2 (expiry)
    rather than step 6 anyway.

    The clock advance is ``PRUNE_GRACE`` longer than it used to be. The prune is
    cluster-wide but runs on the LOCAL clock, so it must not delete a row while
    any other host's clock could still consider it claimable — see PRUNE_GRACE
    and ``test_a_fast_pruner_host_must_not_erase_a_still_valid_claim``. Same
    assertion, later moment; nothing here was loosened.
    """
    clock = [T0]
    store = _store(tmp_path, now=lambda: clock[0])

    assert store.claim("n-1", T0 + timedelta(seconds=30)) is True
    assert store.claim("n-1", T0 + timedelta(seconds=30)) is False
    clock[0] = T0 + PRUNE_GRACE + timedelta(seconds=31)
    store.claim("n-2", T0 + timedelta(seconds=60))  # any claim prunes

    with Session(store.engine) as s:
        rows = s.exec(select(ToolReceiptNonceRow)).all()
    assert [r.nonce for r in rows] == ["n-2"]


def test_offset_aware_expiry_is_converted_not_truncated(tmp_path):
    """SQLite's DATETIME bind silently drops tzinfo, and the gate's ``_utc``
    passes an offset-aware value through untouched. Stored raw, a +02:00
    expiry would persist local wall-clock as though it were UTC and prune a
    still-valid nonce two hours early, reopening the replay window."""
    store = _store(tmp_path, now=lambda: T0)
    expires = T0 + timedelta(seconds=60)
    plus_two = expires.astimezone(timezone(timedelta(hours=2)))

    assert store.claim("n-tz", plus_two) is True
    with Session(store.engine) as s:
        row = s.exec(select(ToolReceiptNonceRow)).one()
    assert row.expires_at.replace(tzinfo=timezone.utc) == expires


def test_default_store_is_unchanged_without_the_env_var(monkeypatch):
    """Opt-in means opt-in: the six existing gate suites and the demo must see
    the same store they saw before this feature existed."""
    from agenttic.gate.middleware import FileNonceStore, _default_nonce_store

    monkeypatch.delenv("AGENTTIC_NONCE_DB", raising=False)
    assert isinstance(_default_nonce_store(), FileNonceStore)
