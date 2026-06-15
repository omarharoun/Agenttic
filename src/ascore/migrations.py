"""Version-tracked schema migrations — an in-repo, dependency-free equivalent
of Alembic, sized for this single-SQLite project.

Each migration is ``(version, name, up(conn))`` applied in order; applied
versions are recorded in a ``schema_migrations`` table, so the schema is
versioned and reproducible rather than drifting via additive ``create_all``.
The baseline (v1) builds the current schema. Future schema changes add a new
numbered migration (explicit DDL / data backfill) — never edit an applied one.

``run_migrations`` is invoked from ``Registry.__init__``, so every tenant DB
self-migrates to head on first use. The ``ascore migrate`` CLI reports/forces it.
(For a Postgres/scale move, this can be swapped for Alembic — see
docs/PRODUCTION_READINESS.md §3.2.)
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlmodel import SQLModel


def _baseline(conn) -> None:
    """v1 — create the full current schema. Importing the model modules
    registers every table (registry + UI) on SQLModel.metadata."""
    import ascore.registry.sqlite_store  # noqa: F401  (registers registry tables)
    import ascore.server.store  # noqa: F401            (registers UI tables)
    SQLModel.metadata.create_all(conn)


# (version, name, up) — append new migrations; never mutate applied ones.
MIGRATIONS: list[tuple[int, str, callable]] = [
    (1, "baseline_schema", _baseline),
]


def _ensure_table(conn) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"))


def applied_versions(conn) -> set[int]:
    _ensure_table(conn)
    return {row[0] for row in conn.execute(text(
        "SELECT version FROM schema_migrations"))}


def run_migrations(engine, migrations=None) -> list[int]:
    """Apply pending migrations in order; return the versions applied."""
    migrations = MIGRATIONS if migrations is None else migrations
    done: list[int] = []
    with engine.begin() as conn:
        have = applied_versions(conn)
        for version, name, up in sorted(migrations):
            if version in have:
                continue
            up(conn)
            conn.execute(
                text("INSERT INTO schema_migrations(version, name, applied_at) "
                     "VALUES (:v, :n, :t)"),
                {"v": version, "n": name,
                 "t": datetime.now(timezone.utc).isoformat()})
            done.append(version)
    return done


def migration_status(engine, migrations=None) -> dict:
    migrations = MIGRATIONS if migrations is None else migrations
    with engine.connect() as conn:
        have = applied_versions(conn)
    versions = [v for v, _, _ in migrations]
    return {"applied": sorted(have),
            "pending": [v for v in versions if v not in have],
            "head": max(versions) if versions else 0}
