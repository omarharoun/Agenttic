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
        run_migrations(engine, [m for m in MIGRATIONS if m[0] < 24])
        with engine.begin() as conn:
            for t in self.EVIDENCE:
                conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        engine.dispose()
        return tmp_path / name

    def test_registry_on_a_legacy_db_can_read_scenario_runs(self, tmp_path):
        # Fails before v24: OperationalError, no such table: scenario_runs.
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

    def test_head_advanced_and_v24_is_recorded(self, tmp_path):
        reg = Registry(self._legacy(tmp_path, "head.db"))
        st = migration_status(reg.engine)
        assert st["pending"] == [] and 24 in st["applied"]
