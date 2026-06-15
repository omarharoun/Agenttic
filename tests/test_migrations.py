"""Versioned migrations + SQLite hardening."""

from sqlalchemy import text
from sqlmodel import SQLModel, create_engine

from ascore.migrations import migration_status, run_migrations
from ascore.registry.sqlite_store import Registry


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
        assert st["applied"] == [1] and st["pending"] == [] and st["head"] == 1

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
                from ascore.schema.agent import DeclaredAgent
                reg.register_agent(DeclaredAgent(agent_id="x", variant="reference"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=writer)
        t.start(); t.join()
        assert errors == []  # check_same_thread=False + busy_timeout
