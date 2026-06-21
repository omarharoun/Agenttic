"""Registry — versioned storage (Step 6), SQLite by default, Postgres-capable.

Principles:
* Append-only versioning: a (suite_id, version) or (rubric_id, version) pair
  can never be overwritten. Updating means saving the next version.
* Scorecards record the exact suite+rubric versions used, so any historical
  run is reproducible.
* Live-path data (production traces, live scores, re-eval requests) lives in
  separate tables and never mixes into batch scorecards (Step 9 criterion).

The only permitted in-place updates are the suite approval flag and the catalog
``active`` flag — gate/catalog state, not content.

**Tenancy.** Every table carries a ``tenant_id``. A Registry is bound to one
tenant and scopes every read/write by it. With SQLite the default deployment is
DB-per-tenant (the file is the boundary; ``tenant_id`` stays "default"); with
Postgres a single database is shared and ``tenant_id`` provides row-level
isolation (see ``server.app.Workspaces``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import UniqueConstraint, event, func
from sqlmodel import Field, Session, SQLModel, create_engine, select

from ascore.schema.agent import DeclaredAgent
from ascore.schema.scorecard import Scorecard
from ascore.schema.testcase import TestCase, TestSuite
from ascore.schema.rubric import Rubric
from ascore.schema.trace import Trace

DEFAULT_TENANT = "default"


class DuplicateVersionError(RuntimeError):
    """Attempted to overwrite an existing (id, version) pair."""


class NotFoundError(KeyError):
    pass


class SuiteRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "suite_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    suite_id: str = Field(index=True)
    version: int
    approved: bool = False
    payload: str


class CaseRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "suite_id", "suite_version",
                                       "test_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    suite_id: str = Field(index=True)
    suite_version: int
    test_id: str
    payload: str


class RubricRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "rubric_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    rubric_id: str = Field(index=True)
    version: int
    payload: str


class DeclaredAgentRow(SQLModel, table=True):
    """The pre-registered agent catalog. Versioned + append-only like suites
    and rubrics — editing an agent stores the next version. ``active`` is the
    one permitted in-place flag (a retire toggle, like the suite approval gate);
    it is catalog state, not connection content."""
    __table_args__ = (UniqueConstraint("tenant_id", "agent_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    agent_id: str = Field(index=True)
    version: int
    active: bool = True
    created_at: datetime
    payload: str


class TraceRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "trace_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    trace_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    mode: str = Field(index=True)  # "batch" | "live"
    created_at: datetime
    payload: str


class ScorecardRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "scorecard_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    scorecard_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    suite_id: str = Field(index=True)
    suite_version: int
    created_at: datetime
    payload: str


class ABComparisonRow(SQLModel, table=True):
    """One A/B comparison run. ``status`` tracks the background run lifecycle
    (running -> succeeded/failed); ``payload`` holds the serialized
    :class:`ascore.schema.ab.ABComparison` once the run completes."""
    __table_args__ = (UniqueConstraint("tenant_id", "comparison_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    comparison_id: str = Field(index=True)
    suite_id: str = Field(index=True)
    status: str = Field(default="running")   # running | succeeded | failed
    error: str = ""
    created_at: datetime
    payload: str = ""                        # ABComparison JSON when done


class LiveScoreRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    agent_id: str = Field(index=True)
    trace_id: str
    criterion_id: str = Field(index=True)
    score: float
    created_at: datetime


class ReEvalRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    agent_id: str = Field(index=True)
    reason: str
    created_at: datetime


class SpendRow(SQLModel, table=True):
    """Append-only ledger of LLM spend, for the daily budget cap."""
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    day: str = Field(index=True)  # UTC YYYY-MM-DD
    model: str
    cost_usd: float
    created_at: datetime


class UserRow(SQLModel, table=True):
    """A login account. GLOBAL table (lookup by email, not tenant-scoped) — the
    user authenticates first, then their ``tenant_id``/``role`` drive the
    existing tenant scoping + RBAC. Passwords are bcrypt hashes, never plaintext."""
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    password_hash: str
    role: str = "viewer"                       # viewer | operator | admin
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    created_at: datetime
    verified: bool = Field(default=False)      # email confirmed via a token


class ApiKeyRow(SQLModel, table=True):
    """A tenant's own provider API key, ENCRYPTED at rest. GLOBAL table keyed by
    (tenant_id, provider). The ciphertext is never returned by the API; only a
    masked ``…last4`` is surfaced. Every Anthropic call for a tenant's run uses
    this key — the platform key is never used for tenant runs."""
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "provider"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(index=True)
    provider: str = "anthropic"
    ciphertext: str
    last4: str
    created_at: datetime
    updated_at: datetime


class EmailTokenRow(SQLModel, table=True):
    """A single-use, expiring email token (account verification). GLOBAL, like
    users. Consumed by setting ``used_at``; rows are safe to prune past expiry."""
    __tablename__ = "email_tokens"
    __table_args__ = (UniqueConstraint("token"),)
    id: int | None = Field(default=None, primary_key=True)
    token: str = Field(index=True)
    email: str = Field(index=True)
    purpose: str = "verify"
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _harden_sqlite(engine) -> None:
    """Per-connection PRAGMAs for safe concurrent access: WAL (concurrent
    readers + one writer), a busy timeout (wait instead of 'database is
    locked'), and foreign-key enforcement. WAL persists on the file; the rest
    are connection-scoped, so set them on every connect."""
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def make_engine(url: str):
    """Build a SQLAlchemy engine for ``url`` (sqlite:/// or postgresql+psycopg://),
    applying SQLite hardening when applicable."""
    is_sqlite = url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(url, connect_args=connect_args)
    if is_sqlite:
        _harden_sqlite(engine)
    return engine


class Registry:
    """Versioned store bound to one tenant. Also satisfies the harness
    TraceStore protocol. Pass ``db_path`` (SQLite file), ``url`` (any backend),
    or a shared ``engine`` (Postgres multi-tenant)."""

    def __init__(self, db_path: str | Path | None = None, *,
                 url: str | None = None, engine=None, tenant: str = DEFAULT_TENANT):
        self.tenant = tenant
        if engine is not None:
            self.engine = engine
        else:
            if url is None:
                url = f"sqlite:///{db_path if db_path is not None else 'ascore.db'}"
            self.engine = make_engine(url)
        from ascore.migrations import run_migrations
        run_migrations(self.engine)  # idempotent; versioned schema

    # -- suites / cases ----------------------------------------------------

    def save_suite(self, suite: TestSuite, cases: list[TestCase]) -> None:
        bad = [c.test_id for c in cases if c.suite_id != suite.suite_id]
        if bad:
            raise ValueError(f"cases not belonging to suite {suite.suite_id}: {bad}")
        with Session(self.engine) as s:
            exists = s.exec(select(SuiteRow).where(
                SuiteRow.tenant_id == self.tenant,
                SuiteRow.suite_id == suite.suite_id,
                SuiteRow.version == suite.version)).first()
            if exists:
                raise DuplicateVersionError(
                    f"suite {suite.suite_id} v{suite.version} already stored; "
                    "save the next version instead"
                )
            s.add(SuiteRow(tenant_id=self.tenant, suite_id=suite.suite_id,
                           version=suite.version, approved=suite.approved,
                           payload=suite.model_dump_json()))
            for c in cases:
                s.add(CaseRow(tenant_id=self.tenant, suite_id=suite.suite_id,
                              suite_version=suite.version, test_id=c.test_id,
                              payload=c.model_dump_json()))
            s.commit()

    def get_suite(self, suite_id: str, version: int | None = None
                  ) -> tuple[TestSuite, list[TestCase]]:
        with Session(self.engine) as s:
            q = select(SuiteRow).where(SuiteRow.tenant_id == self.tenant,
                                       SuiteRow.suite_id == suite_id)
            q = q.where(SuiteRow.version == version) if version is not None \
                else q.order_by(SuiteRow.version.desc())
            row = s.exec(q).first()
            if not row:
                raise NotFoundError(f"suite {suite_id} v{version}")
            suite = TestSuite.model_validate_json(row.payload)
            suite.approved = row.approved
            case_rows = s.exec(select(CaseRow).where(
                CaseRow.tenant_id == self.tenant,
                CaseRow.suite_id == suite_id,
                CaseRow.suite_version == suite.version)).all()
            cases = [TestCase.model_validate_json(r.payload) for r in case_rows]
            return suite, sorted(cases, key=lambda c: c.test_id)

    def add_cases(self, suite_id: str, version: int, cases: list[TestCase]) -> int:
        """Persist generated cases incrementally (generator checkpointing).
        Idempotent: skips (suite_id, version, test_id) already present. Returns
        the number newly inserted. Lets a failed generation resume instead of
        re-spending tokens for already-generated tasks."""
        added = 0
        with Session(self.engine) as s:
            existing = set(s.exec(select(CaseRow.test_id).where(
                CaseRow.tenant_id == self.tenant, CaseRow.suite_id == suite_id,
                CaseRow.suite_version == version)).all())
            for c in cases:
                if c.test_id in existing:
                    continue
                s.add(CaseRow(tenant_id=self.tenant, suite_id=suite_id,
                              suite_version=version, test_id=c.test_id,
                              payload=c.model_dump_json()))
                added += 1
            s.commit()
        return added

    def peek_cases(self, suite_id: str, version: int) -> list[TestCase]:
        """Cases already checkpointed for (suite_id, version) — for resume.
        Empty if none. Does NOT require a SuiteRow to exist yet."""
        with Session(self.engine) as s:
            rows = s.exec(select(CaseRow).where(
                CaseRow.tenant_id == self.tenant, CaseRow.suite_id == suite_id,
                CaseRow.suite_version == version)).all()
            return sorted((TestCase.model_validate_json(r.payload) for r in rows),
                          key=lambda c: c.test_id)

    def finalize_suite(self, suite: TestSuite) -> None:
        """Insert the SuiteRow once all cases are checkpointed (generator end).
        Idempotent — a re-run that completes simply confirms the existing row."""
        with Session(self.engine) as s:
            exists = s.exec(select(SuiteRow).where(
                SuiteRow.tenant_id == self.tenant,
                SuiteRow.suite_id == suite.suite_id,
                SuiteRow.version == suite.version)).first()
            if exists:
                return
            s.add(SuiteRow(tenant_id=self.tenant, suite_id=suite.suite_id,
                           version=suite.version, approved=suite.approved,
                           payload=suite.model_dump_json()))
            s.commit()

    def approve_suite(self, suite_id: str, version: int) -> None:
        with Session(self.engine) as s:
            row = s.exec(select(SuiteRow).where(
                SuiteRow.tenant_id == self.tenant,
                SuiteRow.suite_id == suite_id,
                SuiteRow.version == version)).first()
            if not row:
                raise NotFoundError(f"suite {suite_id} v{version}")
            row.approved = True
            s.add(row)
            s.commit()

    # -- rubrics -------------------------------------------------------------

    def save_rubric(self, rubric: Rubric) -> None:
        with Session(self.engine) as s:
            if s.exec(select(RubricRow).where(
                    RubricRow.tenant_id == self.tenant,
                    RubricRow.rubric_id == rubric.rubric_id,
                    RubricRow.version == rubric.version)).first():
                raise DuplicateVersionError(
                    f"rubric {rubric.rubric_id} v{rubric.version} already stored"
                )
            s.add(RubricRow(tenant_id=self.tenant, rubric_id=rubric.rubric_id,
                            version=rubric.version, payload=rubric.model_dump_json()))
            s.commit()

    def get_rubric(self, rubric_id: str, version: int | None = None) -> Rubric:
        with Session(self.engine) as s:
            q = select(RubricRow).where(RubricRow.tenant_id == self.tenant,
                                        RubricRow.rubric_id == rubric_id)
            q = q.where(RubricRow.version == version) if version is not None \
                else q.order_by(RubricRow.version.desc())
            row = s.exec(q).first()
            if not row:
                raise NotFoundError(f"rubric {rubric_id} v{version}")
            return Rubric.model_validate_json(row.payload)

    # -- declared agent catalog ------------------------------------------------

    def register_agent(self, agent: DeclaredAgent) -> DeclaredAgent:
        """Create or update a catalog entry. Create-or-bump semantics (like
        Managed Agent deploy): a new agent_id starts at v1; re-registering an
        existing one stores the next version and reactivates it. Prior versions
        stay on record (append-only)."""
        with Session(self.engine) as s:
            versions = s.exec(select(DeclaredAgentRow.version).where(
                DeclaredAgentRow.tenant_id == self.tenant,
                DeclaredAgentRow.agent_id == agent.agent_id)).all()
            agent = agent.model_copy(
                update={"version": (max(versions) + 1) if versions else 1})
            s.add(DeclaredAgentRow(
                tenant_id=self.tenant, agent_id=agent.agent_id,
                version=agent.version, active=True,
                created_at=_now(), payload=agent.model_dump_json()))
            s.commit()
        return agent

    def get_declared_agent(self, agent_id: str, version: int | None = None
                           ) -> DeclaredAgent:
        with Session(self.engine) as s:
            q = select(DeclaredAgentRow).where(
                DeclaredAgentRow.tenant_id == self.tenant,
                DeclaredAgentRow.agent_id == agent_id)
            q = q.where(DeclaredAgentRow.version == version) if version is not None \
                else q.order_by(DeclaredAgentRow.version.desc())
            row = s.exec(q).first()
            if not row:
                raise NotFoundError(f"declared agent {agent_id}")
            return DeclaredAgent.model_validate_json(row.payload)

    def list_declared_agents(self, include_retired: bool = False
                             ) -> list[dict]:
        """Latest version of every declared agent. Each dict is the agent's
        fields plus catalog metadata (``active``, ``created_at``)."""
        with Session(self.engine) as s:
            rows = s.exec(select(DeclaredAgentRow).where(
                DeclaredAgentRow.tenant_id == self.tenant)).all()
        latest: dict[str, DeclaredAgentRow] = {}
        for r in rows:
            if r.agent_id not in latest or r.version > latest[r.agent_id].version:
                latest[r.agent_id] = r
        out = []
        for r in sorted(latest.values(), key=lambda r: r.agent_id):
            if not r.active and not include_retired:
                continue
            agent = DeclaredAgent.model_validate_json(r.payload)
            out.append({**agent.model_dump(), "active": r.active,
                        "created_at": r.created_at.isoformat()})
        return out

    def retire_agent(self, agent_id: str) -> None:
        """Soft-delete: flip every version of this agent to inactive. The
        history stays (append-only); re-registering reactivates it."""
        with Session(self.engine) as s:
            rows = s.exec(select(DeclaredAgentRow).where(
                DeclaredAgentRow.tenant_id == self.tenant,
                DeclaredAgentRow.agent_id == agent_id)).all()
            if not rows:
                raise NotFoundError(f"declared agent {agent_id}")
            for r in rows:
                r.active = False
                s.add(r)
            s.commit()

    # -- traces ----------------------------------------------------------------

    def save_trace(self, trace: Trace, mode: str = "batch") -> None:
        with Session(self.engine) as s:
            s.add(TraceRow(tenant_id=self.tenant, trace_id=trace.trace_id,
                           agent_id=trace.agent_id, mode=mode, created_at=_now(),
                           payload=trace.model_dump_json()))
            s.commit()

    def get_trace(self, trace_id: str) -> Trace:
        with Session(self.engine) as s:
            row = s.exec(select(TraceRow).where(
                TraceRow.tenant_id == self.tenant,
                TraceRow.trace_id == trace_id)).first()
            if not row:
                raise NotFoundError(f"trace {trace_id}")
            return Trace.model_validate_json(row.payload)

    def traces(self, agent_id: str, mode: str = "batch") -> list[Trace]:
        with Session(self.engine) as s:
            rows = s.exec(select(TraceRow).where(
                TraceRow.tenant_id == self.tenant,
                TraceRow.agent_id == agent_id, TraceRow.mode == mode)
                .order_by(TraceRow.id)).all()
            return [Trace.model_validate_json(r.payload) for r in rows]

    # -- scorecards --------------------------------------------------------------

    def save_scorecard(self, sc: Scorecard) -> None:
        with Session(self.engine) as s:
            s.add(ScorecardRow(tenant_id=self.tenant, scorecard_id=sc.scorecard_id,
                               agent_id=sc.agent_id, suite_id=sc.suite_id,
                               suite_version=sc.suite_version,
                               created_at=sc.created_at, payload=sc.model_dump_json()))
            s.commit()

    def get_scorecard(self, scorecard_id: str) -> Scorecard:
        with Session(self.engine) as s:
            row = s.exec(select(ScorecardRow).where(
                ScorecardRow.tenant_id == self.tenant,
                ScorecardRow.scorecard_id == scorecard_id)).first()
            if not row:
                raise NotFoundError(f"scorecard {scorecard_id}")
            return Scorecard.model_validate_json(row.payload)

    def scorecards_for(self, agent_id: str, suite_id: str | None = None
                       ) -> list[Scorecard]:
        with Session(self.engine) as s:
            q = select(ScorecardRow).where(ScorecardRow.tenant_id == self.tenant,
                                           ScorecardRow.agent_id == agent_id)
            if suite_id:
                q = q.where(ScorecardRow.suite_id == suite_id)
            rows = s.exec(q.order_by(ScorecardRow.created_at)).all()
            return [Scorecard.model_validate_json(r.payload) for r in rows]

    def scorecards_in(self, suite_ids) -> list["Scorecard"]:
        """All scorecards (any agent) for the given suites, oldest-first."""
        ids = list(suite_ids)
        if not ids:
            return []
        with Session(self.engine) as s:
            rows = s.exec(select(ScorecardRow).where(
                ScorecardRow.tenant_id == self.tenant,
                ScorecardRow.suite_id.in_(ids)).order_by(ScorecardRow.created_at)).all()
            return [Scorecard.model_validate_json(r.payload) for r in rows]

    def suites_scored_for(self, agent_id: str) -> list[str]:
        with Session(self.engine) as s:
            rows = s.exec(select(ScorecardRow.suite_id).where(
                ScorecardRow.tenant_id == self.tenant,
                ScorecardRow.agent_id == agent_id).distinct()).all()
            return list(rows)

    # -- A/B comparisons -------------------------------------------------------

    def create_ab_run(self, comparison_id: str, suite_id: str) -> None:
        """Insert a 'running' placeholder so the UI can track an in-flight A/B
        run before its comparison artifact exists."""
        with Session(self.engine) as s:
            s.add(ABComparisonRow(
                tenant_id=self.tenant, comparison_id=comparison_id,
                suite_id=suite_id, status="running", created_at=_now()))
            s.commit()

    def save_ab_comparison(self, comparison) -> None:
        """Persist a finished comparison. Upserts: completes the 'running' row
        the manager created, or inserts a new 'succeeded' row (CLI/direct use)."""
        from ascore.schema.ab import ABComparison
        assert isinstance(comparison, ABComparison)
        with Session(self.engine) as s:
            row = s.exec(select(ABComparisonRow).where(
                ABComparisonRow.tenant_id == self.tenant,
                ABComparisonRow.comparison_id == comparison.comparison_id)).first()
            if row is None:
                row = ABComparisonRow(
                    tenant_id=self.tenant,
                    comparison_id=comparison.comparison_id,
                    suite_id=comparison.suite_id, created_at=comparison.created_at)
            row.status = "succeeded"
            row.error = ""
            row.suite_id = comparison.suite_id
            row.payload = comparison.model_dump_json()
            s.add(row)
            s.commit()

    def fail_ab_run(self, comparison_id: str, error: str) -> None:
        with Session(self.engine) as s:
            row = s.exec(select(ABComparisonRow).where(
                ABComparisonRow.tenant_id == self.tenant,
                ABComparisonRow.comparison_id == comparison_id)).first()
            if row is None:
                return
            row.status = "failed"
            row.error = error[:500]
            s.add(row)
            s.commit()

    def get_ab_run(self, comparison_id: str) -> dict:
        """Run status + the comparison artifact (parsed, or None while running)."""
        from ascore.schema.ab import ABComparison
        with Session(self.engine) as s:
            row = s.exec(select(ABComparisonRow).where(
                ABComparisonRow.tenant_id == self.tenant,
                ABComparisonRow.comparison_id == comparison_id)).first()
        if row is None:
            raise NotFoundError(f"ab comparison {comparison_id}")
        comp = (ABComparison.model_validate_json(row.payload)
                if row.payload else None)
        return {"comparison_id": row.comparison_id, "suite_id": row.suite_id,
                "status": row.status, "error": row.error,
                "created_at": row.created_at.isoformat(),
                "comparison": comp.model_dump(mode="json") if comp else None}

    def get_ab_comparison(self, comparison_id: str):
        """The finished comparison object (raises if it hasn't completed)."""
        from ascore.schema.ab import ABComparison
        with Session(self.engine) as s:
            row = s.exec(select(ABComparisonRow).where(
                ABComparisonRow.tenant_id == self.tenant,
                ABComparisonRow.comparison_id == comparison_id)).first()
        if row is None or not row.payload:
            raise NotFoundError(f"ab comparison {comparison_id}")
        return ABComparison.model_validate_json(row.payload)

    def list_ab_runs(self, suite_id: str | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(ABComparisonRow).where(
                ABComparisonRow.tenant_id == self.tenant)
            if suite_id:
                q = q.where(ABComparisonRow.suite_id == suite_id)
            rows = s.exec(q.order_by(ABComparisonRow.created_at.desc())).all()
        from ascore.schema.ab import ABComparison
        out = []
        for r in rows:
            summary = {"comparison_id": r.comparison_id, "suite_id": r.suite_id,
                       "status": r.status, "error": r.error,
                       "created_at": r.created_at.isoformat(),
                       "label_a": None, "label_b": None, "winner": None,
                       "verdict": None}
            if r.payload:
                c = ABComparison.model_validate_json(r.payload)
                summary.update(label_a=c.label_a, label_b=c.label_b,
                               winner=c.winner, verdict=c.verdict,
                               success_rate_a=c.success_rate_a,
                               success_rate_b=c.success_rate_b,
                               n_paired=c.n_paired)
            out.append(summary)
        return out

    # -- live path (Step 9) ----------------------------------------------------

    def save_live_scores(self, agent_id: str, trace_id: str,
                         scores: dict[str, float]) -> None:
        with Session(self.engine) as s:
            for cid, val in scores.items():
                s.add(LiveScoreRow(tenant_id=self.tenant, agent_id=agent_id,
                                   trace_id=trace_id, criterion_id=cid, score=val,
                                   created_at=_now()))
            s.commit()

    def live_scores(self, agent_id: str, criterion_id: str, last_n: int
                    ) -> list[float]:
        with Session(self.engine) as s:
            rows = s.exec(select(LiveScoreRow).where(
                LiveScoreRow.tenant_id == self.tenant,
                LiveScoreRow.agent_id == agent_id,
                LiveScoreRow.criterion_id == criterion_id)
                .order_by(LiveScoreRow.id.desc()).limit(last_n)).all()
            return [r.score for r in rows]

    def save_reeval_request(self, agent_id: str, reason: str) -> None:
        with Session(self.engine) as s:
            s.add(ReEvalRow(tenant_id=self.tenant, agent_id=agent_id,
                            reason=reason, created_at=_now()))
            s.commit()

    def reeval_requests(self, agent_id: str) -> list[str]:
        with Session(self.engine) as s:
            rows = s.exec(select(ReEvalRow).where(
                ReEvalRow.tenant_id == self.tenant,
                ReEvalRow.agent_id == agent_id).order_by(ReEvalRow.id)).all()
            return [r.reason for r in rows]

    # -- spend ledger (budget caps) --------------------------------------------

    def record_spend(self, model: str, cost_usd: float) -> None:
        if not cost_usd:
            return
        now = _now()
        with Session(self.engine) as s:
            s.add(SpendRow(tenant_id=self.tenant, day=now.strftime("%Y-%m-%d"),
                           model=model, cost_usd=cost_usd, created_at=now))
            s.commit()

    def spend_today(self) -> float:
        return self.spend_since_days(0)

    def spend_since_days(self, days: int) -> float:
        """Total spend over the trailing ``days`` days (0 => just today)."""
        from datetime import timedelta
        start = (_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with Session(self.engine) as s:
            total = s.exec(select(func.sum(SpendRow.cost_usd)).where(
                SpendRow.tenant_id == self.tenant, SpendRow.day >= start)).one()
            return float(total or 0.0)

    # -- retention --------------------------------------------------------------

    def prune_traces(self, older_than_days: int) -> int:
        """Delete trace rows (this tenant) older than ``older_than_days``.
        Returns the number removed. Live + batch alike; scorecards keep their
        aggregates, so historical results survive."""
        if older_than_days <= 0:
            return 0
        from datetime import timedelta
        cutoff = _now() - timedelta(days=older_than_days)
        with Session(self.engine) as s:
            rows = s.exec(select(TraceRow).where(
                TraceRow.tenant_id == self.tenant,
                TraceRow.created_at < cutoff)).all()
            for r in rows:
                s.delete(r)
            s.commit()
            return len(rows)

    def redact_old_traces(self, older_than_days: int) -> int:
        """Strip span inputs/outputs and final_output from traces (this tenant)
        older than ``older_than_days`` — a PII control that keeps the trace row
        (timing/cost/structure) while dropping the potentially-sensitive
        payloads. Returns the number redacted. Idempotent."""
        if older_than_days <= 0:
            return 0
        from datetime import timedelta
        cutoff = _now() - timedelta(days=older_than_days)
        n = 0
        with Session(self.engine) as s:
            rows = s.exec(select(TraceRow).where(
                TraceRow.tenant_id == self.tenant,
                TraceRow.created_at < cutoff)).all()
            for r in rows:
                p = json.loads(r.payload)
                for span in p.get("spans", []):
                    span["input"] = {}
                    span["output"] = {}
                    if span.get("error"):
                        span["error"] = "[redacted]"
                if p.get("final_output"):
                    p["final_output"] = "[redacted]"
                r.payload = json.dumps(p)
                s.add(r)
                n += 1
            s.commit()
            return n
