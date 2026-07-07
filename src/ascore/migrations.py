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


_TENANT_TABLES = [
    "suiterow", "caserow", "rubricrow", "declaredagentrow", "tracerow",
    "scorecardrow", "livescorerow", "reevalrow", "spendrow",
    "workflowrow", "executionrow", "executioneventrow",
]


def _add_tenant_id(conn) -> None:
    """v2 — add tenant_id to any table created before tenancy (pre-existing v1
    DBs). Fresh DBs already have it from the baseline, so this is a no-op there.
    Portable across SQLite and Postgres (checks columns via the inspector)."""
    from sqlalchemy import inspect
    insp = inspect(conn)
    existing = set(insp.get_table_names())
    for table in _TENANT_TABLES:
        if table not in existing:
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if "tenant_id" not in cols:
            conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN tenant_id VARCHAR "
                "DEFAULT 'default'"))


def _users_table(conn) -> None:
    """v3 — the login-accounts table. Fresh DBs get it from the baseline;
    this creates it on DBs already at v2 (idempotent via checkfirst)."""
    import ascore.registry.sqlite_store  # noqa: F401 (registers UserRow)
    from ascore.registry.sqlite_store import UserRow
    UserRow.__table__.create(bind=conn, checkfirst=True)


def _email_verification(conn) -> None:
    """v4 — email verification. Add ``users.verified`` and the email_tokens
    table. Existing accounts predate verification, so they're backfilled to
    verified=1 (never locks out the bootstrapped admin)."""
    from sqlalchemy import inspect

    import ascore.registry.sqlite_store  # noqa: F401 (registers EmailTokenRow)
    from ascore.registry.sqlite_store import EmailTokenRow

    is_pg = conn.dialect.name == "postgresql"
    default, truth = ("false", "true") if is_pg else ("0", "1")
    insp = inspect(conn)
    if "users" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("users")}
        if "verified" not in cols:
            conn.execute(text(
                f"ALTER TABLE users ADD COLUMN verified BOOLEAN DEFAULT {default}"))
            conn.execute(text(f"UPDATE users SET verified = {truth}"))  # trust pre-existing
    EmailTokenRow.__table__.create(bind=conn, checkfirst=True)


def _api_keys_table(conn) -> None:
    """v5 — per-tenant provider API keys (encrypted at rest)."""
    import ascore.registry.sqlite_store  # noqa: F401 (registers ApiKeyRow)
    from ascore.registry.sqlite_store import ApiKeyRow
    ApiKeyRow.__table__.create(bind=conn, checkfirst=True)


def _ab_comparisons_table(conn) -> None:
    """v6 — A/B comparison runs (two variants, head-to-head on one suite)."""
    import ascore.registry.sqlite_store  # noqa: F401 (registers ABComparisonRow)
    from ascore.registry.sqlite_store import ABComparisonRow
    ABComparisonRow.__table__.create(bind=conn, checkfirst=True)


def _canonical_runs_table(conn) -> None:
    """v7 — standard-benchmark canonical runs (pass^k + ECE + index per agent)."""
    import ascore.registry.sqlite_store  # noqa: F401 (registers CanonicalRunRow)
    from ascore.registry.sqlite_store import CanonicalRunRow
    CanonicalRunRow.__table__.create(bind=conn, checkfirst=True)


def _optimization_runs_table(conn) -> None:
    """v8 — prompt-optimization runs (baseline→best system-prompt lineage +
    train/heldout scores from the self-improving loop)."""
    import ascore.registry.sqlite_store  # noqa: F401 (registers OptimizationRunRow)
    from ascore.registry.sqlite_store import OptimizationRunRow
    OptimizationRunRow.__table__.create(bind=conn, checkfirst=True)


def _personal_api_tokens_table(conn) -> None:
    """v9 — personal API tokens (PATs): per-user programmatic REST access,
    stored hashed; a PAT authenticates as the owning user's tenant + role."""
    import ascore.registry.sqlite_store  # noqa: F401 (registers PersonalApiTokenRow)
    from ascore.registry.sqlite_store import PersonalApiTokenRow
    PersonalApiTokenRow.__table__.create(bind=conn, checkfirst=True)


def _result_cache_table(conn) -> None:
    """v10 — result cache: deterministic run fingerprint -> completed result,
    per tenant, so identical runs reuse a scorecard instead of re-spending."""
    import ascore.registry.sqlite_store  # noqa: F401 (registers ResultCacheRow)
    from ascore.registry.sqlite_store import ResultCacheRow
    ResultCacheRow.__table__.create(bind=conn, checkfirst=True)


def _certifications_table(conn) -> None:
    """v11 — Agent Safety Certifications: signed, publicly-verifiable safety
    grades issued from a completed safety scorecard. GLOBAL table keyed by
    cert_id (tenant_id scopes issuance); public read by id ignores tenant."""
    import ascore.registry.sqlite_store  # noqa: F401 (registers CertificationRow)
    from ascore.registry.sqlite_store import CertificationRow
    CertificationRow.__table__.create(bind=conn, checkfirst=True)


def _agent_connections_table(conn) -> None:
    """v12 — "Connect your agent" configs: a tenant's live HTTP endpoint +
    request/response mapping for the Safety Battery scan. The auth header value
    is stored encrypted; ``consent`` gates scanning."""
    import ascore.registry.sqlite_store  # noqa: F401 (registers AgentConnectionRow)
    from ascore.registry.sqlite_store import AgentConnectionRow
    AgentConnectionRow.__table__.create(bind=conn, checkfirst=True)


def _assistant_sessions_table(conn) -> None:
    """v13 — Safe Reference Assistant sessions: tenant-scoped conversation
    state (transcript, scratchpad, step log, pending approval gate)."""
    import ascore.registry.sqlite_store  # noqa: F401 (registers AssistantSessionRow)
    from ascore.registry.sqlite_store import AssistantSessionRow
    AssistantSessionRow.__table__.create(bind=conn, checkfirst=True)


def _training_camp_tables(conn) -> None:
    """v14 — Training Camp (folded-in AgentCamp): tenant-scoped training/eval
    runs and their graded-episode memory. ``CampRunRow`` holds the config, the
    Wilson-lower-bound accuracy, the promotion-gate decision + human sign-off,
    and the improve-loop ratchet log; ``CampEpisodeRow`` is the reusable memory
    the distillation export and review queue read from."""
    import ascore.camp.store  # noqa: F401 (registers CampRunRow + CampEpisodeRow)
    from ascore.camp.store import CampEpisodeRow, CampRunRow
    CampRunRow.__table__.create(bind=conn, checkfirst=True)
    CampEpisodeRow.__table__.create(bind=conn, checkfirst=True)


def _training_camp_async_progress(conn) -> None:
    """v15 — async camp runs: add live-progress + heartbeat columns to
    camprunrow (total/completed episodes, phase, updated_at). Additive; on a
    fresh DB v14 already creates these from the current model, so we add only
    the columns that are actually missing (idempotent across SQLite/Postgres)."""
    from sqlalchemy import inspect
    existing = {c["name"] for c in inspect(conn).get_columns("camprunrow")}
    adds = {
        "total_episodes": "INTEGER NOT NULL DEFAULT 0",
        "episodes_completed": "INTEGER NOT NULL DEFAULT 0",
        "phase": "VARCHAR NOT NULL DEFAULT ''",
        "updated_at": "TIMESTAMP",
    }
    for name, ddl in adds.items():
        if name not in existing:
            conn.execute(text(f"ALTER TABLE camprunrow ADD COLUMN {name} {ddl}"))


def _certification_track_tables(conn) -> None:
    """v16 — SPEC-2 certification track: cert_profiles, dossiers +
    dossier_events, incidents + incident_events. The *_events tables are
    append-only; state is folded from them."""
    import ascore.registry.sqlite_store  # noqa: F401
    from ascore.registry.sqlite_store import (
        CertProfileRow, DossierEventRow, DossierRow, IncidentEventRow,
        IncidentRow,
    )
    for model in (CertProfileRow, DossierRow, DossierEventRow,
                  IncidentRow, IncidentEventRow):
        model.__table__.create(bind=conn, checkfirst=True)


# (version, name, up) — append new migrations; never mutate applied ones.
MIGRATIONS: list[tuple[int, str, callable]] = [
    (1, "baseline_schema", _baseline),
    (2, "add_tenant_id", _add_tenant_id),
    (3, "users_table", _users_table),
    (4, "email_verification", _email_verification),
    (5, "api_keys_table", _api_keys_table),
    (6, "ab_comparisons_table", _ab_comparisons_table),
    (7, "canonical_runs_table", _canonical_runs_table),
    (8, "optimization_runs_table", _optimization_runs_table),
    (9, "personal_api_tokens_table", _personal_api_tokens_table),
    (10, "result_cache_table", _result_cache_table),
    (11, "certifications_table", _certifications_table),
    (12, "agent_connections_table", _agent_connections_table),
    (13, "assistant_sessions_table", _assistant_sessions_table),
    (14, "training_camp_tables", _training_camp_tables),
    (15, "training_camp_async_progress", _training_camp_async_progress),
    (16, "certification_track_tables", _certification_track_tables),
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
