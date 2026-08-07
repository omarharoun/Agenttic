"""Cold-start migrations must survive concurrent first-touch.

Every command migrates the registry when it builds one. Point N processes at a
FRESH database and they all read an empty `schema_migrations`, all run the same
migration, and all try to insert the same version.

Measured before the fix: 8 parallel `certify --mock` into a new db gave 4 clean
exits and 4 raw `IntegrityError: UNIQUE constraint failed:
schema_migrations.version`. Not a write-lock problem — pre-initialise the db and
8/8 concurrent writers succeed, because SQLite's WAL plus `busy_timeout` handles
steady-state writes. It was only the bootstrap.

A prior session reasoned that "concurrent registry writes are the same thing the
harness already does; SQLite is WAL with a busy timeout" and concluded they were
safe. That holds for steady state and is false at cold start — which is why this
file tests the cold start specifically.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from agenttic.migrations import MIGRATIONS, applied_versions, run_migrations
from agenttic.registry.sqlite_store import make_engine


def _fresh(tmp_path, name="cold.db"):
    return make_engine(f"sqlite:///{tmp_path / name}")


class TestConcurrentColdStart:
    @pytest.mark.parametrize("workers", [4, 8])
    def test_no_worker_crashes_racing_the_bootstrap(self, tmp_path, workers):
        """The headline. Every worker must come back clean, not half of them."""
        engine = _fresh(tmp_path, f"cold{workers}.db")
        errors: list[BaseException] = []

        def go():
            try:
                run_migrations(engine)
            except BaseException as exc:      # noqa: BLE001 — recording it IS the test
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda _: go(), range(workers)))

        assert not errors, f"{len(errors)}/{workers} crashed: {errors[:2]}"

    def test_every_migration_is_applied_exactly_once(self, tmp_path):
        """A tolerated collision must not leave a version applied twice, or the
        `schema_migrations` table stops being a truthful record."""
        engine = _fresh(tmp_path)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: run_migrations(engine), range(8)))

        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT version, COUNT(*) FROM schema_migrations "
                     "GROUP BY version HAVING COUNT(*) > 1")).all()
        assert rows == [], f"versions applied more than once: {rows}"

    def test_the_schema_reaches_head(self, tmp_path):
        engine = _fresh(tmp_path)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: run_migrations(engine), range(8)))
        with engine.connect() as conn:
            have = set(applied_versions(conn))
        assert have == {v for v, _, _ in MIGRATIONS}

    def test_no_version_is_claimed_by_two_workers(self, tmp_path):
        """`run_migrations` returns what THIS caller applied, and no version may
        appear in two callers' returns — that would mean the same migration ran
        twice and both believed they owned it.

        It does NOT assert that every version is claimed by someone. Measured: it
        is not. Under a 6-way race the baseline is applied by a worker whose own
        DDL then loses to a sibling, so the version lands in `schema_migrations`
        while no caller reports having applied it. The return value is a report
        of THIS call's work, not a ledger of the database — the ledger is
        `schema_migrations`, and `test_the_schema_reaches_head` checks that.
        """
        engine = _fresh(tmp_path)
        with ThreadPoolExecutor(max_workers=6) as pool:
            claimed = list(pool.map(lambda _: run_migrations(engine), range(6)))
        flat = [v for batch in claimed for v in batch]
        assert len(flat) == len(set(flat)), f"a version was claimed twice: {flat}"


class TestSteadyStateWasNeverTheProblem:
    def test_a_pre_initialised_db_takes_concurrent_migrate_calls(self, tmp_path):
        """Pinning the diagnosis: with the bootstrap already done, concurrency
        was always fine. If this ever fails the cause is a write-lock issue, not
        the migration race, and the fix is somewhere else entirely."""
        engine = _fresh(tmp_path, "warm.db")
        run_migrations(engine)                      # serial bootstrap first
        errors: list[BaseException] = []

        def go():
            try:
                assert run_migrations(engine) == []  # nothing left to apply
            except BaseException as exc:             # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: go(), range(8)))
        assert not errors, errors[:2]


class TestIdempotence:
    def test_running_twice_applies_nothing_the_second_time(self, tmp_path):
        engine = _fresh(tmp_path)
        first = run_migrations(engine)
        second = run_migrations(engine)
        assert first and second == []
