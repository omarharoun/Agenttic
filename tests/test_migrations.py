"""Versioned migrations + SQLite hardening."""

from sqlalchemy import text
from sqlmodel import SQLModel, create_engine

from agenttic.migrations import migration_status, run_migrations
from agenttic.registry.sqlite_store import Registry


def _tables(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))
        return {r[0] for r in rows}


class TestRunner:
    def test_registry_runs_baseline(self, tmp_path):
        reg = Registry(tmp_path / "m.db")
        tables = _tables(reg.engine)
        # baseline created core registry + UI tables and the tracking table
        assert {"suiterow", "scorecardrow", "declaredagentrow", "spendrow",
                "workflowrow", "schema_migrations"} <= {t.lower() for t in tables}
        st = migration_status(reg.engine)
        assert st["pending"] == [] and st["head"] >= 1 and 1 in st["applied"]

    def test_idempotent(self, tmp_path):
        reg = Registry(tmp_path / "m.db")
        assert run_migrations(reg.engine) == []  # nothing pending on a 2nd run

    def test_custom_migration_applies_once(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}")

        def add_widget(conn):
            conn.execute(text("CREATE TABLE widget (id INTEGER PRIMARY KEY)"))

        migs = [(1, "base", lambda conn: None), (2, "widget", add_widget)]
        assert run_migrations(engine, migs) == [1, 2]
        assert "widget" in _tables(engine)
        # re-running applies nothing
        assert run_migrations(engine, migs) == []
        assert migration_status(engine, migs)["pending"] == []


class TestHardening:
    def test_pragmas_set_on_connection(self, tmp_path):
        reg = Registry(tmp_path / "h.db")
        with reg.engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000

    def test_cross_thread_write_does_not_error(self, tmp_path):
        import threading
        reg = Registry(tmp_path / "t.db")
        errors = []

        def writer():
            try:
                from agenttic.schema.agent import DeclaredAgent
                reg.register_agent(DeclaredAgent(agent_id="x", variant="reference"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=writer)
        t.start(); t.join()
        assert errors == []  # check_same_thread=False + busy_timeout


class TestLegacyDatabaseGetsTheEvidenceTables:
    """A DB created before these row classes existed is at head WITHOUT them.

    v1 is ``create_all``, so a fresh DB has every table whether or not a
    migration names it — which is why this gap stayed invisible. The failing
    case is a database that ran v1 when the class did not exist: migrations
    never re-run v1, so only an explicit migration can add it.
    """

    EVIDENCE = ["scenario_spaces", "coverage_models", "assertion_sets",
                "honeypot_batteries", "scenario_runs"]

    def _legacy(self, tmp_path, name="legacy.db"):
        """A database at head with the evidence tables absent — what an older
        deployment actually looks like."""
        from agenttic.migrations import MIGRATIONS
        engine = create_engine(f"sqlite:///{tmp_path / name}")
        run_migrations(engine, [m for m in MIGRATIONS if m[0] < 30])
        with engine.begin() as conn:
            for t in self.EVIDENCE:
                conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        engine.dispose()
        return tmp_path / name

    def test_registry_on_a_legacy_db_can_read_scenario_runs(self, tmp_path):
        # Fails before the migration: OperationalError, no such table.
        # Registry.__init__ runs migrations and NOT create_all, so the CLI has
        # no second chance the way the server's UIStore does.
        reg = Registry(self._legacy(tmp_path))
        assert reg.list_scenario_runs() == []

    def test_all_five_evidence_tables_are_restored(self, tmp_path):
        reg = Registry(self._legacy(tmp_path, "five.db"))
        assert set(self.EVIDENCE) <= _tables(reg.engine)

    def test_it_does_not_disturb_a_database_that_already_has_them(self, tmp_path):
        reg = Registry(tmp_path / "fresh.db")
        before = _tables(reg.engine)
        assert run_migrations(reg.engine) == []       # already at head
        assert _tables(reg.engine) == before          # checkfirst=True, no churn

    def test_head_advanced_and_the_migration_is_recorded(self, tmp_path):
        reg = Registry(self._legacy(tmp_path, "head.db"))
        st = migration_status(reg.engine)
        assert st["pending"] == [] and 30 in st["applied"]


class TestTheBurnedVersionRange:
    """Production has 24-29 applied from code in no branch of this repo.

    `run_migrations` skips any version already recorded, so a migration reusing
    one of those numbers is skipped THERE forever while applying everywhere else
    — the same migration meaning two different schemas depending on which
    codebase last touched the database. The number is an identity.
    """

    BURNED = range(24, 30)

    def test_no_migration_reuses_a_burned_version(self):
        from agenttic.migrations import MIGRATIONS
        clash = [(v, n) for v, n, _ in MIGRATIONS if v in self.BURNED]
        assert clash == [], (
            f"migration(s) {clash} reuse a version already applied in "
            "production by other code; they would never run there")

    def test_versions_are_unique_and_ordered(self):
        from agenttic.migrations import MIGRATIONS
        versions = [v for v, _, _ in MIGRATIONS]
        assert versions == sorted(versions)
        assert len(versions) == len(set(versions))

    def test_a_database_stuck_at_the_burned_head_still_gets_the_tables(
            self, tmp_path):
        """The real production shape: head 29, evidence tables absent. The
        migration must still apply, which is the whole reason for renumbering."""
        from sqlalchemy import text as _text

        from agenttic.migrations import MIGRATIONS
        engine = create_engine(f"sqlite:///{tmp_path / 'burned.db'}")
        run_migrations(engine, [m for m in MIGRATIONS if m[0] < 30])
        with engine.begin() as conn:
            for t in ("scenario_runs", "scenario_spaces", "coverage_models",
                      "assertion_sets", "honeypot_batteries"):
                conn.execute(_text(f"DROP TABLE IF EXISTS {t}"))
            # simulate the other codebase having taken 24-29
            for v in self.BURNED:
                conn.execute(_text(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    f"VALUES ({v}, 'other_codebase_{v}', 'x')"))
        engine.dispose()

        reg = Registry(tmp_path / "burned.db")
        assert "scenario_runs" in _tables(reg.engine)
        assert reg.list_scenario_runs() == []
