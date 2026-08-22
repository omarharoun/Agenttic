"""ADVERSARY: attacking "a receipt nonce can be claimed exactly once,
cluster-wide" against ``DbNonceStore``.

Everything here is an exploit attempt. The ones that were BLOCKED stay as
regressions asserting the block. The two at the bottom are NOT blocked: they
are the failing proofs of a real replay, and they are left failing on purpose.

The harness is imported from ``test_gate_nonce_store`` for the same reason the
DbNonceStore suite imports it — one instrument, so a difference in the verdict
is a difference in the store.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError as DBIntegrityError
from sqlmodel import Session, select

from agenttic.config import load_config
from agenttic.migrations import run_migrations
from agenttic.passport.keys import PassportKeyManager, generate_key
from agenttic.registry.sqlite_store import (
    DbNonceStore,
    IntegrityError as DomainIntegrityError,
    ToolReceiptNonceRow,
    make_engine,
)
from tests.test_gate_nonce_store import T0, _headers, _worker

CFG = load_config("config.yaml")

# The irreversible TTL the receipts in ``_headers`` are minted with
# (gate.receipt.IRREVERSIBLE_TTL_SECONDS). Named here so the skew tests below
# say what they mean instead of hiding a magic number.
RECEIPT_TTL = timedelta(seconds=30)


def _url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'nonces.db'}"


def _store(tmp_path, *, now) -> DbNonceStore:
    """One host. Its own engine, its own clock, one shared database file."""
    return DbNonceStore(_url(tmp_path), now=now)


def _race(*claims) -> list:
    """Release N claimers on one barrier and collect their verdicts.

    A raise is not a verdict: it is captured and returned as itself, so a store
    that blows up under contention cannot be mistaken for one that refused.
    """
    gate = threading.Barrier(len(claims))
    results: list = [None] * len(claims)

    def run(i, claim):
        gate.wait(timeout=10)
        try:
            results[i] = claim()
        except BaseException as exc:  # noqa: BLE001 - surfacing it IS the point
            results[i] = exc

    threads = [threading.Thread(target=run, args=(i, c))
               for i, c in enumerate(claims)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def _rows(store) -> list[str]:
    with Session(store.engine) as s:
        return sorted(r.nonce for r in s.exec(select(ToolReceiptNonceRow)).all())


# --------------------------------------------------------------------------- #
# BLOCKED — the attacks that failed, kept as regressions.
# --------------------------------------------------------------------------- #


def test_sixteen_hosts_racing_one_nonce_produce_one_winner(tmp_path):
    """Widen the two-host race to sixteen engines on one file.

    Two threads can serialise by luck; sixteen released on a barrier cannot all
    serialise quietly. Exceptions are asserted absent as well as double claims:
    a store whose losers raise ``database is locked`` instead of returning
    ``False`` fails closed but turns every legitimate first use into a coin
    flip, so it would still be a finding.
    """
    stores = [_store(tmp_path, now=lambda: T0) for _ in range(16)]
    expires = T0 + timedelta(seconds=60)

    verdicts = _race(*[
        (lambda st=st: st.claim("n-16", expires)) for st in stores])

    raised = [v for v in verdicts if isinstance(v, BaseException)]
    assert not raised, f"a claimer raised instead of returning a verdict: {raised}"
    assert verdicts.count(True) == 1, verdicts
    assert verdicts.count(False) == 15, verdicts
    assert _rows(stores[0]) == ["n-16"]


def test_a_table_without_the_constraint_double_claims(tmp_path):
    """POSITIVE CONTROL, and the one the existing suite is missing.

    ``_CheckThenInsertStore`` in ``test_gate_nonce_store_db`` is an in-memory
    set, so it only proves the barrier works. This control is the real thing:
    the same engine, the same prune-insert-commit-catch-IntegrityError control
    flow, on a table whose ONLY difference is that ``UNIQUE(nonce)`` is absent.
    It double-claims through the identical harness, which is what makes
    "sixteen hosts, one winner" above a statement about the constraint rather
    than about threading.
    """
    control = _NoConstraintStore(_url(tmp_path), now=lambda: T0)
    expires = T0 + timedelta(seconds=60)

    verdicts = _race(lambda: control.claim("n-race", expires),
                     lambda: control.claim("n-race", expires))
    assert verdicts == [True, True], \
        f"the control was supposed to be broken, got {verdicts}"
    assert control.count("n-race") == 2


class _NoConstraintStore:
    """``DbNonceStore.claim`` with the UNIQUE constraint removed and nothing
    else changed."""

    def __init__(self, url: str, *, now) -> None:
        self.engine = make_engine(url)
        self._now = now
        with self.engine.begin() as c:
            c.execute(text("CREATE TABLE IF NOT EXISTS unconstrained_nonces "
                           "(nonce TEXT, expires_at TEXT, claimed_at TEXT)"))

    def claim(self, nonce: str, expires_at: datetime) -> bool:
        now = self._now()
        with Session(self.engine) as s:
            s.execute(text("DELETE FROM unconstrained_nonces "
                           "WHERE expires_at <= :now"), {"now": now.isoformat()})
            s.execute(text("INSERT INTO unconstrained_nonces VALUES "
                           "(:n, :e, :c)"),
                      {"n": nonce, "e": expires_at.isoformat(),
                       "c": now.isoformat()})
            try:
                s.commit()
            except DBIntegrityError:
                s.rollback()
                return False
        return True

    def count(self, nonce: str) -> int:
        with self.engine.begin() as c:
            return c.execute(text("SELECT COUNT(*) FROM unconstrained_nonces "
                                  "WHERE nonce = :n"), {"n": nonce}).scalar_one()


def test_a_losing_claim_leaves_the_winners_row_exactly_as_it_found_it(tmp_path):
    """The rollback must not be a partial one.

    ``claim`` deletes expired rows and inserts in ONE transaction, so a loser's
    rollback has to undo its prune too. If the delete survived the rollback, a
    burst of replays would be a nonce-table eraser: each loser reports 403 and
    quietly clears somebody else's still-valid claim on the way out.
    """
    clock = [T0]
    store = _store(tmp_path, now=lambda: clock[0])
    victim_expiry = T0 + timedelta(seconds=600)
    assert store.claim("victim", victim_expiry) is True
    assert store.claim("target", T0 + timedelta(seconds=600)) is True

    for _ in range(5):  # five losing replays, five rollbacks
        assert store.claim("target", T0 + timedelta(seconds=600)) is False

    assert _rows(store) == ["target", "victim"]
    assert store.claim("victim", victim_expiry) is False, \
        "a losing claim's rollback did not restore the row it pruned"


def test_a_non_database_error_never_reports_a_claim(tmp_path):
    """The wrong-``IntegrityError`` attack, both directions.

    ``sqlite_store`` defines its own ``IntegrityError`` (a suite failing its
    gates) that SHADOWS ``sqlalchemy.exc.IntegrityError`` in that module — the
    exact confusion that once made ``_append_only`` leak raw sqlite errors. If
    ``claim`` caught the domain class, a genuine UNIQUE conflict would escape;
    if it caught too broadly, an unrelated failure would be read as a verdict.
    Neither may happen: anything that is not the database refusing a duplicate
    row must propagate, so the gate turns it into a 403 rather than a pass.
    """
    store = _store(tmp_path, now=lambda: T0)

    def boom(*_a, **_k):
        raise DomainIntegrityError("a suite failed its integrity gates")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Session, "commit", boom)
        with pytest.raises(DomainIntegrityError):
            store.claim("n-domain", T0 + timedelta(seconds=60))

    assert _rows(store) == [], "a failed claim persisted a row"
    # And the escape hatch is not a claim either: nothing was recorded, so the
    # nonce is still unspent rather than silently spent-or-not.
    assert store.claim("n-domain", T0 + timedelta(seconds=60)) is True


def test_a_second_tenant_cannot_reclaim_a_spent_nonce(tmp_path):
    """Tenant scoping, attacked from the direction that matters.

    The table is deliberately global — no ``tenant_id``. So the attack is
    whether a replay presented as another tenant gets a fresh claim. It must
    not: the nonce IS the claim, and per-tenant keying would license exactly
    one replay per tenant.
    """
    tenant_a = _store(tmp_path, now=lambda: T0)
    tenant_b = _store(tmp_path, now=lambda: T0)
    expires = T0 + timedelta(seconds=60)

    assert tenant_a.claim("n-cross", expires) is True
    assert tenant_b.claim("n-cross", expires) is False, \
        "the nonce was re-claimable under a second tenant"

    with Session(tenant_a.engine) as s:
        cols = {c["name"] for c in s.connection().engine.dialect.get_columns(
            s.connection(), "tool_receipt_nonces")}
    assert "tenant_id" not in cols, \
        "a tenant column would make the claim per-tenant, which is the bug"


def test_a_still_valid_nonce_survives_every_prune(tmp_path):
    """Prune must delete only what is genuinely past expiry.

    Walk the clock right up to the last microsecond before expiry, pruning at
    each step, and the claim must still be there. An off-by-one (``<`` where
    ``<=`` belongs, or a truncating round-trip through SQLite's DATETIME) hands
    back a replay window.
    """
    clock = [T0]
    store = _store(tmp_path, now=lambda: clock[0])
    expires = T0 + timedelta(seconds=30)
    assert store.claim("n-1", expires) is True

    for offset in (timedelta(0), timedelta(seconds=15),
                   timedelta(seconds=29, microseconds=999999)):
        clock[0] = T0 + offset
        store.claim(f"filler-{offset}", T0 + timedelta(seconds=600))
        assert "n-1" in _rows(store), f"pruned {offset} before expiry"
        assert store.claim("n-1", expires) is False, f"replay allowed at {offset}"


def test_migration_v38_repairs_a_populated_database_at_the_old_head(tmp_path):
    """The migration attacked on a database that already has data.

    A fresh DB gets the table from the v1 baseline, so the interesting case is
    the one v38 exists for: an existing registry, already at the old head, with
    rows in it. It must add the table and its unique index without disturbing
    anything, and be idempotent on a re-run.
    """
    engine = make_engine(_url(tmp_path))
    run_migrations(engine)
    with engine.begin() as c:
        c.execute(text("INSERT INTO suiterow (tenant_id, suite_id, version, "
                       "approved, payload) VALUES "
                       "('default', 's-1', 1, 0, '{}')"))
        # Rewind to the old head: the table did not exist before v38.
        c.execute(text("DROP TABLE tool_receipt_nonces"))
        c.execute(text("DELETE FROM schema_migrations WHERE version = 38"))

    assert run_migrations(engine) == [38]
    assert run_migrations(engine) == [], "v38 is not idempotent"

    with engine.begin() as c:
        assert c.execute(text("SELECT COUNT(*) FROM suiterow")).scalar_one() == 1
        indexes = {r[1] for r in c.execute(text(
            "PRAGMA index_list('tool_receipt_nonces')"))}
    assert any("autoindex" in i for i in indexes), \
        f"UNIQUE(nonce) did not survive the migration: {indexes}"

    # And the repaired table actually arbitrates.
    store = DbNonceStore(_url(tmp_path), now=lambda: T0)
    assert store.claim("n-migrated", T0 + timedelta(seconds=60)) is True
    assert store.claim("n-migrated", T0 + timedelta(seconds=60)) is False


# --------------------------------------------------------------------------- #
# NOT BLOCKED — a real replay. These two fail. Do not delete them to go green.
#
# The prune inside ``claim`` is a CLUSTER-WIDE destructive operation driven by
# ONE host's local clock, with no skew margin at all, while step 2 of
# ``verify_tool_receipt`` budgets ``DEFAULT_SKEW_SECONDS = 5`` for precisely
# the skew that this store's own docstring exists to span ("the gateway host is
# not the tool host, so the clocks differ"). Any host whose clock runs ahead
# deletes rows that are still inside every other host's validity window, and
# the receipts they were holding become replayable there.
#
# FileNonceStore prunes the same way, but it is arbitrated by one kernel with
# one clock, so the divergence cannot arise. Making the claim cluster-wide is
# what introduced a second clock into the delete.
# --------------------------------------------------------------------------- #


def test_a_fast_pruner_host_must_not_erase_a_still_valid_claim(tmp_path):
    """One host's clock is a minute fast. Its ordinary traffic — a claim for an
    unrelated nonce — prunes a receipt that every other host still considers
    valid, and the replay walks back in on the accurate host.

    No race, no barrier, no contrived interleaving: only clock skew between two
    machines sharing a database, which is the deployment this store is for.
    """
    accurate = _store(tmp_path, now=lambda: T0)
    fast = _store(tmp_path, now=lambda: T0 + RECEIPT_TTL + timedelta(seconds=1))
    expires = T0 + RECEIPT_TTL

    assert accurate.claim("n-1", expires) is True
    fast.claim("unrelated", T0 + timedelta(seconds=600))  # ordinary traffic

    assert accurate.claim("n-1", expires) is False, \
        "the fast host's prune erased a nonce still inside the receipt's window"


def test_skewed_pruner_lets_the_irreversible_action_run_twice(tmp_path):
    """The same defect end to end, so the cost is observed and not inferred.

    Both hosts verify the receipt on the same clock — it is genuinely unexpired
    at both — and the only difference is that host B's system clock is 31s
    fast, so its prune runs ahead of every verifier's expiry check. The
    customer is deleted twice from one single-use receipt.
    """
    keys = PassportKeyManager(CFG, private_key=generate_key())
    executions: list[str] = []
    slow_store = _store(tmp_path, now=lambda: T0)
    fast_store = _store(
        tmp_path, now=lambda: T0 + RECEIPT_TTL + timedelta(seconds=1))
    a = _worker(keys, slow_store, executions)

    headers = _headers(keys)
    assert a.delete("/customers/c-1", headers=headers).status_code == 200
    # Host B serving one ordinary request of its own: any successful claim on
    # the fast host prunes, and the prune is cluster-wide.
    fast_store.claim("host-b-own-traffic", T0 + timedelta(seconds=600))
    second = a.delete("/customers/c-1", headers=headers)

    assert second.status_code == 403, "the receipt was single-use"
    assert executions == ["c-1"], \
        f"the irreversible action ran more than once: {executions}"
