"""Registry — versioned SQLite storage (Step 6).

Principles:
* Append-only versioning: a (suite_id, version) or (rubric_id, version) pair
  can never be overwritten. Updating means saving the next version.
* Scorecards record the exact suite+rubric versions used, so any historical
  run is reproducible.
* Live-path data (production traces, live scores, re-eval requests) lives in
  separate tables and never mixes into batch scorecards (Step 9 criterion).

The only permitted in-place update is the suite approval flag (the Step 8
human gate) — gate state, not content.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine, select

from ascore.schema.agent import DeclaredAgent
from ascore.schema.scorecard import Scorecard
from ascore.schema.testcase import TestCase, TestSuite
from ascore.schema.rubric import Rubric
from ascore.schema.trace import Trace


class DuplicateVersionError(RuntimeError):
    """Attempted to overwrite an existing (id, version) pair."""


class NotFoundError(KeyError):
    pass


class SuiteRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("suite_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    suite_id: str = Field(index=True)
    version: int
    approved: bool = False
    payload: str


class CaseRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("suite_id", "suite_version", "test_id"),)
    id: int | None = Field(default=None, primary_key=True)
    suite_id: str = Field(index=True)
    suite_version: int
    test_id: str
    payload: str


class RubricRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("rubric_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    rubric_id: str = Field(index=True)
    version: int
    payload: str


class DeclaredAgentRow(SQLModel, table=True):
    """The pre-registered agent catalog. Versioned + append-only like suites
    and rubrics — editing an agent stores the next version. ``active`` is the
    one permitted in-place flag (a retire toggle, like the suite approval gate);
    it is catalog state, not connection content."""
    __table_args__ = (UniqueConstraint("agent_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    version: int
    active: bool = True
    created_at: datetime
    payload: str


class TraceRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    trace_id: str = Field(index=True, unique=True)
    agent_id: str = Field(index=True)
    mode: str = Field(index=True)  # "batch" | "live"
    created_at: datetime
    payload: str


class ScorecardRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    scorecard_id: str = Field(index=True, unique=True)
    agent_id: str = Field(index=True)
    suite_id: str = Field(index=True)
    suite_version: int
    created_at: datetime
    payload: str


class LiveScoreRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    trace_id: str
    criterion_id: str = Field(index=True)
    score: float
    created_at: datetime


class ReEvalRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    reason: str
    created_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Registry:
    """SQLite-backed store. Also satisfies the harness TraceStore protocol."""

    def __init__(self, db_path: str | Path = "ascore.db"):
        self.engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(self.engine)

    # -- suites / cases ----------------------------------------------------

    def save_suite(self, suite: TestSuite, cases: list[TestCase]) -> None:
        bad = [c.test_id for c in cases if c.suite_id != suite.suite_id]
        if bad:
            raise ValueError(f"cases not belonging to suite {suite.suite_id}: {bad}")
        with Session(self.engine) as s:
            exists = s.exec(select(SuiteRow).where(
                SuiteRow.suite_id == suite.suite_id,
                SuiteRow.version == suite.version)).first()
            if exists:
                raise DuplicateVersionError(
                    f"suite {suite.suite_id} v{suite.version} already stored; "
                    "save the next version instead"
                )
            s.add(SuiteRow(suite_id=suite.suite_id, version=suite.version,
                           approved=suite.approved, payload=suite.model_dump_json()))
            for c in cases:
                s.add(CaseRow(suite_id=suite.suite_id, suite_version=suite.version,
                              test_id=c.test_id, payload=c.model_dump_json()))
            s.commit()

    def get_suite(self, suite_id: str, version: int | None = None
                  ) -> tuple[TestSuite, list[TestCase]]:
        with Session(self.engine) as s:
            q = select(SuiteRow).where(SuiteRow.suite_id == suite_id)
            q = q.where(SuiteRow.version == version) if version is not None \
                else q.order_by(SuiteRow.version.desc())
            row = s.exec(q).first()
            if not row:
                raise NotFoundError(f"suite {suite_id} v{version}")
            suite = TestSuite.model_validate_json(row.payload)
            suite.approved = row.approved
            case_rows = s.exec(select(CaseRow).where(
                CaseRow.suite_id == suite_id,
                CaseRow.suite_version == suite.version)).all()
            cases = [TestCase.model_validate_json(r.payload) for r in case_rows]
            return suite, sorted(cases, key=lambda c: c.test_id)

    def approve_suite(self, suite_id: str, version: int) -> None:
        with Session(self.engine) as s:
            row = s.exec(select(SuiteRow).where(
                SuiteRow.suite_id == suite_id, SuiteRow.version == version)).first()
            if not row:
                raise NotFoundError(f"suite {suite_id} v{version}")
            row.approved = True
            s.add(row)
            s.commit()

    # -- rubrics -------------------------------------------------------------

    def save_rubric(self, rubric: Rubric) -> None:
        with Session(self.engine) as s:
            if s.exec(select(RubricRow).where(
                    RubricRow.rubric_id == rubric.rubric_id,
                    RubricRow.version == rubric.version)).first():
                raise DuplicateVersionError(
                    f"rubric {rubric.rubric_id} v{rubric.version} already stored"
                )
            s.add(RubricRow(rubric_id=rubric.rubric_id, version=rubric.version,
                            payload=rubric.model_dump_json()))
            s.commit()

    def get_rubric(self, rubric_id: str, version: int | None = None) -> Rubric:
        with Session(self.engine) as s:
            q = select(RubricRow).where(RubricRow.rubric_id == rubric_id)
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
                DeclaredAgentRow.agent_id == agent.agent_id)).all()
            agent = agent.model_copy(
                update={"version": (max(versions) + 1) if versions else 1})
            s.add(DeclaredAgentRow(
                agent_id=agent.agent_id, version=agent.version, active=True,
                created_at=_now(), payload=agent.model_dump_json()))
            s.commit()
        return agent

    def get_declared_agent(self, agent_id: str, version: int | None = None
                           ) -> DeclaredAgent:
        with Session(self.engine) as s:
            q = select(DeclaredAgentRow).where(
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
            rows = s.exec(select(DeclaredAgentRow)).all()
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
            s.add(TraceRow(trace_id=trace.trace_id, agent_id=trace.agent_id,
                           mode=mode, created_at=_now(),
                           payload=trace.model_dump_json()))
            s.commit()

    def get_trace(self, trace_id: str) -> Trace:
        with Session(self.engine) as s:
            row = s.exec(select(TraceRow).where(TraceRow.trace_id == trace_id)).first()
            if not row:
                raise NotFoundError(f"trace {trace_id}")
            return Trace.model_validate_json(row.payload)

    def traces(self, agent_id: str, mode: str = "batch") -> list[Trace]:
        with Session(self.engine) as s:
            rows = s.exec(select(TraceRow).where(
                TraceRow.agent_id == agent_id, TraceRow.mode == mode)
                .order_by(TraceRow.id)).all()
            return [Trace.model_validate_json(r.payload) for r in rows]

    # -- scorecards --------------------------------------------------------------

    def save_scorecard(self, sc: Scorecard) -> None:
        with Session(self.engine) as s:
            s.add(ScorecardRow(scorecard_id=sc.scorecard_id, agent_id=sc.agent_id,
                               suite_id=sc.suite_id, suite_version=sc.suite_version,
                               created_at=sc.created_at, payload=sc.model_dump_json()))
            s.commit()

    def get_scorecard(self, scorecard_id: str) -> Scorecard:
        with Session(self.engine) as s:
            row = s.exec(select(ScorecardRow).where(
                ScorecardRow.scorecard_id == scorecard_id)).first()
            if not row:
                raise NotFoundError(f"scorecard {scorecard_id}")
            return Scorecard.model_validate_json(row.payload)

    def scorecards_for(self, agent_id: str, suite_id: str | None = None
                       ) -> list[Scorecard]:
        with Session(self.engine) as s:
            q = select(ScorecardRow).where(ScorecardRow.agent_id == agent_id)
            if suite_id:
                q = q.where(ScorecardRow.suite_id == suite_id)
            rows = s.exec(q.order_by(ScorecardRow.created_at)).all()
            return [Scorecard.model_validate_json(r.payload) for r in rows]

    def suites_scored_for(self, agent_id: str) -> list[str]:
        with Session(self.engine) as s:
            rows = s.exec(select(ScorecardRow.suite_id).where(
                ScorecardRow.agent_id == agent_id).distinct()).all()
            return list(rows)

    # -- live path (Step 9) ----------------------------------------------------

    def save_live_scores(self, agent_id: str, trace_id: str,
                         scores: dict[str, float]) -> None:
        with Session(self.engine) as s:
            for cid, val in scores.items():
                s.add(LiveScoreRow(agent_id=agent_id, trace_id=trace_id,
                                   criterion_id=cid, score=val, created_at=_now()))
            s.commit()

    def live_scores(self, agent_id: str, criterion_id: str, last_n: int
                    ) -> list[float]:
        with Session(self.engine) as s:
            rows = s.exec(select(LiveScoreRow).where(
                LiveScoreRow.agent_id == agent_id,
                LiveScoreRow.criterion_id == criterion_id)
                .order_by(LiveScoreRow.id.desc()).limit(last_n)).all()
            return [r.score for r in rows]

    def save_reeval_request(self, agent_id: str, reason: str) -> None:
        with Session(self.engine) as s:
            s.add(ReEvalRow(agent_id=agent_id, reason=reason, created_at=_now()))
            s.commit()

    def reeval_requests(self, agent_id: str) -> list[str]:
        with Session(self.engine) as s:
            rows = s.exec(select(ReEvalRow).where(
                ReEvalRow.agent_id == agent_id).order_by(ReEvalRow.id)).all()
            return [r.reason for r in rows]
