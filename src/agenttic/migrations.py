"""Version-tracked schema migrations — an in-repo, dependency-free equivalent
of Alembic, sized for this single-SQLite project.

Each migration is ``(version, name, up(conn))`` applied in order; applied
versions are recorded in a ``schema_migrations`` table, so the schema is
versioned and reproducible rather than drifting via additive ``create_all``.
The baseline (v1) builds the current schema. Future schema changes add a new
numbered migration (explicit DDL / data backfill) — never edit an applied one.

``run_migrations`` is invoked from ``Registry.__init__``, so every tenant DB
self-migrates to head on first use. The ``agenttic migrate`` CLI reports/forces it.
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
    import agenttic.registry.sqlite_store  # noqa: F401  (registers registry tables)
    import agenttic.server.store  # noqa: F401            (registers UI tables)
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
    import agenttic.registry.sqlite_store  # noqa: F401 (registers UserRow)
    from agenttic.registry.sqlite_store import UserRow
    UserRow.__table__.create(bind=conn, checkfirst=True)


def _email_verification(conn) -> None:
    """v4 — email verification. Add ``users.verified`` and the email_tokens
    table. Existing accounts predate verification, so they're backfilled to
    verified=1 (never locks out the bootstrapped admin)."""
    from sqlalchemy import inspect

    import agenttic.registry.sqlite_store  # noqa: F401 (registers EmailTokenRow)
    from agenttic.registry.sqlite_store import EmailTokenRow

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
    import agenttic.registry.sqlite_store  # noqa: F401 (registers ApiKeyRow)
    from agenttic.registry.sqlite_store import ApiKeyRow
    ApiKeyRow.__table__.create(bind=conn, checkfirst=True)


def _ab_comparisons_table(conn) -> None:
    """v6 — A/B comparison runs (two variants, head-to-head on one suite)."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers ABComparisonRow)
    from agenttic.registry.sqlite_store import ABComparisonRow
    ABComparisonRow.__table__.create(bind=conn, checkfirst=True)


def _canonical_runs_table(conn) -> None:
    """v7 — standard-benchmark canonical runs (pass^k + ECE + index per agent)."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers CanonicalRunRow)
    from agenttic.registry.sqlite_store import CanonicalRunRow
    CanonicalRunRow.__table__.create(bind=conn, checkfirst=True)


def _optimization_runs_table(conn) -> None:
    """v8 — prompt-optimization runs (baseline→best system-prompt lineage +
    train/heldout scores from the self-improving loop)."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers OptimizationRunRow)
    from agenttic.registry.sqlite_store import OptimizationRunRow
    OptimizationRunRow.__table__.create(bind=conn, checkfirst=True)


def _personal_api_tokens_table(conn) -> None:
    """v9 — personal API tokens (PATs): per-user programmatic REST access,
    stored hashed; a PAT authenticates as the owning user's tenant + role."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers PersonalApiTokenRow)
    from agenttic.registry.sqlite_store import PersonalApiTokenRow
    PersonalApiTokenRow.__table__.create(bind=conn, checkfirst=True)


def _result_cache_table(conn) -> None:
    """v10 — result cache: deterministic run fingerprint -> completed result,
    per tenant, so identical runs reuse a scorecard instead of re-spending."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers ResultCacheRow)
    from agenttic.registry.sqlite_store import ResultCacheRow
    ResultCacheRow.__table__.create(bind=conn, checkfirst=True)


def _certifications_table(conn) -> None:
    """v11 — Agent Safety Certifications: signed, publicly-verifiable safety
    grades issued from a completed safety scorecard. GLOBAL table keyed by
    cert_id (tenant_id scopes issuance); public read by id ignores tenant."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers CertificationRow)
    from agenttic.registry.sqlite_store import CertificationRow
    CertificationRow.__table__.create(bind=conn, checkfirst=True)


def _agent_connections_table(conn) -> None:
    """v12 — "Connect your agent" configs: a tenant's live HTTP endpoint +
    request/response mapping for the Safety Battery scan. The auth header value
    is stored encrypted; ``consent`` gates scanning."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers AgentConnectionRow)
    from agenttic.registry.sqlite_store import AgentConnectionRow
    AgentConnectionRow.__table__.create(bind=conn, checkfirst=True)


def _assistant_sessions_table(conn) -> None:
    """v13 — Safe Reference Assistant sessions: tenant-scoped conversation
    state (transcript, scratchpad, step log, pending approval gate)."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers AssistantSessionRow)
    from agenttic.registry.sqlite_store import AssistantSessionRow
    AssistantSessionRow.__table__.create(bind=conn, checkfirst=True)


def _training_camp_tables(conn) -> None:
    """v14 — Training Camp (folded-in AgentCamp): tenant-scoped training/eval
    runs and their graded-episode memory. ``CampRunRow`` holds the config, the
    Wilson-lower-bound accuracy, the promotion-gate decision + human sign-off,
    and the improve-loop ratchet log; ``CampEpisodeRow`` is the reusable memory
    the distillation export and review queue read from."""
    import agenttic.camp.store  # noqa: F401 (registers CampRunRow + CampEpisodeRow)
    from agenttic.camp.store import CampEpisodeRow, CampRunRow
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
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import (
        CertProfileRow, DossierEventRow, DossierRow, IncidentEventRow,
        IncidentRow,
    )
    for model in (CertProfileRow, DossierRow, DossierEventRow,
                  IncidentRow, IncidentEventRow):
        model.__table__.create(bind=conn, checkfirst=True)


def _elicitation_summaries_table(conn) -> None:
    """v17 — append-only elicitation-matrix summaries per agent (T13.5)."""
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import ElicitationSummaryRow
    ElicitationSummaryRow.__table__.create(bind=conn, checkfirst=True)


def _agent_cards_table(conn) -> None:
    """v18 — append-only, versioned agent cards (SPEC-2 M9)."""
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import AgentCardRow
    AgentCardRow.__table__.create(bind=conn, checkfirst=True)


def _enforcement_tables(conn) -> None:
    """v19 — enforcement policies, append-only events, approvals (SPEC-2 M11)."""
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import (
        ApprovalRequestRow, EnforcementEventRow, EnforcementPolicyRow,
    )
    for model in (EnforcementPolicyRow, EnforcementEventRow, ApprovalRequestRow):
        model.__table__.create(bind=conn, checkfirst=True)


def _release_ladder_tables(conn) -> None:
    """v20 — release cohorts + append-only promotion records (SPEC-2 M14)."""
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import CohortRow, PromotionRecordRow
    for model in (CohortRow, PromotionRecordRow):
        model.__table__.create(bind=conn, checkfirst=True)


def _canary_sets_table(conn) -> None:
    """v21 — per-agent versioned canary sets (SPEC-2 M15)."""
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import CanarySetRow
    CanarySetRow.__table__.create(bind=conn, checkfirst=True)


def _passport_tables(conn) -> None:
    """v22 — passports + append-only passport events (SPEC-2 M16)."""
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import PassportEventRow, PassportRow
    for model in (PassportRow, PassportEventRow):
        model.__table__.create(bind=conn, checkfirst=True)


def _copilot_sessions_table(conn) -> None:
    """v23 — in-app Copilot agent sessions (tenant-scoped transcript + step log +
    any write-action awaiting the user's confirmation)."""
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import CopilotSessionRow
    CopilotSessionRow.__table__.create(bind=conn, checkfirst=True)


def _verification_evidence_tables(conn) -> None:
    """v30 — the five verification-evidence tables that were declared after the
    v1 baseline ran and never given a migration of their own.

    A database created before each class existed is at head and does not have the
    table: ``run_migrations`` never re-runs v1, so nothing adds it. The SERVER
    survives this by accident — ``UIStore.__init__`` calls ``create_all`` on the
    registry engine on every workspace build — but ``Registry.__init__`` runs
    migrations and nothing else, so the CLI (and any library caller) raises
    ``OperationalError: no such table`` instead. That accident is exactly the
    ``create_all`` drift this module's docstring says it exists to replace.

    ``checkfirst=True``, so this is a no-op wherever the drift already won."""
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import (
        AssertionSetRow, CoverageModelRow, HoneypotBatteryRow, ScenarioRunRow,
        ScenarioSpaceRow,
    )
    for model in (ScenarioSpaceRow, CoverageModelRow, AssertionSetRow,
                  HoneypotBatteryRow, ScenarioRunRow):
        model.__table__.create(bind=conn, checkfirst=True)


def _gaming_reports_table(conn) -> None:
    """v31 — Evaluation-Gaming Resistance (feat/egr): one row per EGR run keyed
    by execution_id, holding the serialized GamingReport so the
    ``/executions/{id}/gaming`` endpoint can serve it.

    Numbered 31, not the 16 the branch carried: master reached 16 with
    `certification_track_tables` in the month this sat unmerged. Applying the
    branch number would have put two different migrations at one version.
    """
    import agenttic.registry.sqlite_store  # noqa: F401 (registers GamingReportRow)
    from agenttic.registry.sqlite_store import GamingReportRow
    GamingReportRow.__table__.create(bind=conn, checkfirst=True)
def _feedback_table(conn) -> None:
    """v24 — human feedback on traces (SPEC-2 Step 11). Append-only; the
    ``processed`` flag lets the feedback→tests miner (Step 13) mine each item
    exactly once."""
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import FeedbackRow
    FeedbackRow.__table__.create(bind=conn, checkfirst=True)


def _agent_config_table(conn) -> None:
    """v33 — the agent-config promotion ledger (SPEC-2 Step 14, Hard Rule 10):
    every candidate the learning optimizer produced (promoted / rejected /
    pending human approval), chained to its parent so the config family tree is
    auditable."""
    import agenttic.registry.sqlite_store  # noqa: F401
    from agenttic.registry.sqlite_store import AgentConfigRow
    AgentConfigRow.__table__.create(bind=conn, checkfirst=True)


def _seed_judge_configs(conn) -> None:
    """v34 — judge configs become versioned artifacts (SPEC-3 Step 15.1).

    Create the ``judgeconfigrow`` table, then EAGERLY seed one v1
    ``status='active'`` :class:`JudgeConfig` for every judge-scored criterion
    discoverable in existing rubrics (RubricRow payloads). The seed config
    reproduces today's judge prompt byte-for-byte. Idempotent: a criterion that
    already has ANY config row is skipped, so re-running (or seeding a criterion
    the live judge already persisted lazily) never double-inserts. Fresh DBs
    have no rubrics yet, so this simply creates the table; the live judge then
    seeds lazily on first use via ``LLMJudge``."""
    import json as _json

    import agenttic.registry.sqlite_store  # noqa: F401 (registers JudgeConfigRow)
    from agenttic.registry.sqlite_store import JudgeConfigRow
    from agenttic.schema.judge_config import seed_config_for

    JudgeConfigRow.__table__.create(bind=conn, checkfirst=True)

    from sqlalchemy import inspect
    insp = inspect(conn)
    if "rubricrow" not in insp.get_table_names():
        return

    # (tenant_id, criterion_id) pairs of judge-scored criteria across all rubrics.
    seen: set[tuple[str, str]] = set()
    for tenant_id, payload in conn.execute(text(
            "SELECT tenant_id, payload FROM rubricrow")):
        try:
            rubric = _json.loads(payload)
        except Exception:  # noqa: BLE001 — a bad row must not abort the migration
            continue
        for crit in rubric.get("criteria", []) or []:
            if crit.get("scorer") == "judge":
                seen.add((tenant_id or "default", crit.get("criterion_id")))

    for tenant_id, criterion_id in sorted(seen):
        if not criterion_id:
            continue
        # Idempotent: skip if this (tenant, criterion) already has any config.
        exists = conn.execute(text(
            "SELECT 1 FROM judgeconfigrow WHERE tenant_id = :t "
            "AND criterion_id = :c LIMIT 1"),
            {"t": tenant_id, "c": criterion_id}).first()
        if exists:
            continue
        cfg = seed_config_for(criterion_id)
        conn.execute(text(
            "INSERT INTO judgeconfigrow "
            "(tenant_id, judge_config_id, criterion_id, version, status, "
            " created_at, payload) VALUES "
            "(:t, :jid, :c, :v, :s, :ca, :p)"),
            {"t": tenant_id, "jid": cfg.judge_config_id, "c": criterion_id,
             "v": cfg.version, "s": cfg.status,
             "ca": cfg.created_at.isoformat(), "p": cfg.model_dump_json()})


def _calibration_splits_table(conn) -> None:
    """v35 — frozen train/held-out calibration splits (SPEC-3 Step 15.2, Hard
    Rule 15). One row per (tenant, criterion_id, seed, trace_id) records whether
    a labeled trace is TRAIN or HELD-OUT, so every optimization round for a
    criterion reuses the SAME held-out benchmark (extend, never reshuffle)."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers CalibrationSplitRow)
    from agenttic.registry.sqlite_store import CalibrationSplitRow
    CalibrationSplitRow.__table__.create(bind=conn, checkfirst=True)


def _judge_optimization_requests_table(conn) -> None:
    """v36 — judge-optimization requests (SPEC-3 Step 15.4). One row per filed
    "please re-optimize this judge" request. Detection is automatic (the
    calibration flywheel notices via ``mine_labels``); the fix stays on-command
    (``learn-judge`` clears the request). At most one open row per criterion."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers the row)
    from agenttic.registry.sqlite_store import JudgeOptimizationRequestRow
    JudgeOptimizationRequestRow.__table__.create(bind=conn, checkfirst=True)


def _generated_suite_snapshots_table(conn) -> None:
    """v37 — generator draft snapshots (SPEC-3 Step 16). One row per
    (tenant, suite_id, version) capturing the "as-generated" case set, plus the
    review diff computed at approval time. Only GENERATOR drafts write here, so
    generator quality (edit_rate) is measurable; other suites simply have no
    row and their diff reports as "unavailable"."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers the row)
    from agenttic.registry.sqlite_store import GeneratedSuiteSnapshotRow
    GeneratedSuiteSnapshotRow.__table__.create(bind=conn, checkfirst=True)


def _tool_receipt_nonces_table(conn) -> None:
    """v38 — spent Tool Access Receipt nonces (RECEIPT-SCHEMA.md §7). Single-use
    lifted from one host to every host sharing the database, via UNIQUE(nonce).
    A fresh DB already gets the table from the v1 baseline; this exists for
    databases already at head."""
    import agenttic.registry.sqlite_store  # noqa: F401 (registers the row)
    from agenttic.registry.sqlite_store import ToolReceiptNonceRow
    ToolReceiptNonceRow.__table__.create(bind=conn, checkfirst=True)


# (version, name, up) — append new migrations; never mutate applied ones.
MIGRATIONS: list[tuple[int, str, callable]] = [
    # 24-29 are BURNED. node1's production database has them applied
    # (feedback_table, agent_config_table, seed_judge_configs,
    # calibration_splits_table, judge_optimization_requests_table,
    # generated_suite_snapshots_table, stamped 2026-07-19). The code behind
    # them was not in any branch when that was written; it is here now, as
    # 32-37 below — but the numbers stay spent. `run_migrations` skips any
    # version already in `schema_migrations`, so reusing 24-29 would be
    # silently skipped THERE forever and the tables would never be created
    # by the mechanism that is supposed to create them.
    #
    # Renumbering costs node1 one no-op re-run — each is `create ...
    # checkfirst=True` against a table it already has — and buys a version
    # that means the same thing on every database. The number is an identity.
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
    (17, "elicitation_summaries_table", _elicitation_summaries_table),
    (18, "agent_cards_table", _agent_cards_table),
    (19, "enforcement_tables", _enforcement_tables),
    (20, "release_ladder_tables", _release_ladder_tables),
    (21, "canary_sets_table", _canary_sets_table),
    (22, "passport_tables", _passport_tables),
    (23, "copilot_sessions_table", _copilot_sessions_table),
    # 24-29 are BURNED. node1's production database has them applied
    # (feedback_table, agent_config_table, seed_judge_configs,
    # calibration_splits_table, judge_optimization_requests_table,
    # generated_suite_snapshots_table, stamped 2026-07-19). The code behind them
    # was in no branch when that was written; it IS here now, as 32-37 below —
    # but the numbers stay spent. `run_migrations` skips any version already in
    # `schema_migrations`, so reusing 24-29 would be silently skipped THERE
    # forever and the tables would never be created by the mechanism that is
    # supposed to create them.
    #
    # Renumbering costs node1 one no-op re-run — each is `create ...
    # checkfirst=True` against a table it already has — and buys a version that
    # means the same thing on every database. The number is an identity.
    (30, "verification_evidence_tables", _verification_evidence_tables),
    (31, "gaming_reports_table", _gaming_reports_table),
    (32, "feedback_table", _feedback_table),
    (33, "agent_config_table", _agent_config_table),
    (34, "seed_judge_configs", _seed_judge_configs),
    (35, "calibration_splits_table", _calibration_splits_table),
    (36, "judge_optimization_requests_table",
     _judge_optimization_requests_table),
    (37, "generated_suite_snapshots_table",
     _generated_suite_snapshots_table),
    (38, "tool_receipt_nonces_table", _tool_receipt_nonces_table),
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
    """Apply pending migrations in order; return the versions applied.

    SAFE UNDER CONCURRENT COLD START. Every command migrates the registry when it
    builds one, so N processes against a FRESH database all read an empty
    `schema_migrations`, all run the same migration, and all try to insert the
    same version. Measured before this: 8 parallel `certify --mock` into a new
    db produced 4 clean exits and 4 raw
    `IntegrityError: UNIQUE constraint failed: schema_migrations.version`.

    Two changes make that safe, and both are needed:

    * **One transaction per migration**, not one for the whole run. Previously a
      single `engine.begin()` wrapped every migration, so one collision aborted
      the entire batch — including migrations that had already succeeded in that
      transaction.
    * **A losing insert is not an error.** If another process registered the
      version first, that process also ran the same `up()`; the work is done.
      We re-read the applied set and carry on rather than crashing.

    A racing process can lose in TWO ways, and tolerating only one is why a
    first attempt at this still failed under threads: it can lose the INSERT
    (IntegrityError on the unique version) or it can lose the DDL itself
    (OperationalError "table ... already exists", because the baseline migration
    is not written with IF NOT EXISTS). Both mean the same thing — someone else
    got there first — so both are tolerated, and only those two.

    Deliberately NOT a lock: losing is harmless once both cases are handled, and
    a lock would serialise every process's startup to buy nothing.
    """
    from sqlalchemy.exc import IntegrityError, OperationalError

    migrations = MIGRATIONS if migrations is None else migrations
    done: list[int] = []
    with engine.connect() as conn:
        have = set(applied_versions(conn))

    for version, name, up in sorted(migrations):
        if version in have:
            continue
        try:
            with engine.begin() as conn:
                up(conn)
                conn.execute(
                    text("INSERT INTO schema_migrations(version, name, applied_at) "
                         "VALUES (:v, :n, :t)"),
                    {"v": version, "n": name,
                     "t": datetime.now(timezone.utc).isoformat()})
            done.append(version)
        except (IntegrityError, OperationalError) as exc:
            # Lost the race — either on the version insert (IntegrityError) or
            # on the DDL (OperationalError "already exists"). Any OTHER
            # OperationalError is a real fault (locked, disk I/O, corrupt) and
            # must NOT be swallowed: that would turn a broken database into a
            # silent success, which is the class of bug this whole change is
            # about.
            if isinstance(exc, OperationalError) and \
                    "already exists" not in str(exc).lower():
                raise
            # Losing the DDL rolled back OUR transaction, including the version
            # row. If the winner's row is not there either — every worker can
            # lose to a sibling — the ledger would be silently short and a later
            # run would re-apply an already-applied migration. Record it, and
            # tolerate losing that race too.
            with engine.connect() as conn:
                have = set(applied_versions(conn))
            if version not in have:
                try:
                    with engine.begin() as conn:
                        conn.execute(
                            text("INSERT INTO schema_migrations"
                                 "(version, name, applied_at) VALUES (:v, :n, :t)"),
                            {"v": version, "n": name,
                             "t": datetime.now(timezone.utc).isoformat()})
                    have.add(version)
                except IntegrityError:
                    have.add(version)   # someone else recorded it first
    return done


def migration_status(engine, migrations=None) -> dict:
    migrations = MIGRATIONS if migrations is None else migrations
    with engine.connect() as conn:
        have = applied_versions(conn)
    versions = [v for v, _, _ in migrations]
    return {"applied": sorted(have),
            "pending": [v for v in versions if v not in have],
            "head": max(versions) if versions else 0}
