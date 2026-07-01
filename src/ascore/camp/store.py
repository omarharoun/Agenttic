"""
Per-tenant persistence for training-camp runs.

Two tenant-scoped tables, following the same conventions as
:mod:`ascore.server.store` (``tenant_id`` on every row, a ``UniqueConstraint``
including the tenant, JSON blobs for structured payloads):

- ``CampRunRow``   — one camp run: config, the ``CampReport`` numbers (passes,
  episodes, Wilson 95% lower bound), the promotion-gate decision, the human
  sign-off (which authenticated operator approved, and when), and — for improve
  runs — the round-by-round ratchet log and holdout numbers.
- ``CampEpisodeRow`` — the memory: every graded episode (inputs, action, grade
  detail). This is what the distillation export and the review queue read from.

A ``CampStore`` is bound to one tenant and filters/stamps by it, exactly like
``UIStore``. It shares the Registry's engine.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Session, SQLModel, select

from ascore.registry.sqlite_store import DEFAULT_TENANT, NotFoundError

from .trace import Episode


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CampRunRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "run_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    run_id: str = Field(index=True)
    kind: str = "single"  # "single" | "improve"
    task_id: str = Field(index=True)
    agent_label: str = ""
    mode: str = "mock"  # "mock" | "agent" (BYO-key agent under camp)
    status: str = "complete"  # complete | error
    created_at: datetime
    finished_at: datetime | None = None
    # config knobs (threshold is the hard, non-overridable accuracy floor)
    threshold: float = 0.99
    min_episodes_for_gate: int = 200
    episodes: int = 0
    passes: int = 0
    seed: int = 0
    # results
    wilson_lower_95: float = 0.0
    pass_rate: float = 0.0
    report: str = "{}"       # full CampReport-derived dict
    gate: str = "{}"         # GateDecision: {promoted, reasons, floor_met, ...}
    rounds: str = "[]"       # improve loop: per-round ratchet log
    review_queue: str = "[]" # holdout failures = the human curriculum
    # human sign-off (the required, real second condition of the gate)
    approved_by: str | None = None
    approved_at: datetime | None = None
    error: str | None = None


class CampEpisodeRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "run_id", "episode_id"),)
    id: int | None = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT, index=True)
    run_id: str = Field(index=True)
    episode_id: str
    task_id: str
    agent_id: str
    passed: bool = False
    score: float = 0.0
    inputs: str = "{}"
    action: str = "{}"
    grade_detail: str = "{}"
    system_prompt: str = ""


def _episode_to_row(tenant: str, run_id: str, ep: Episode) -> CampEpisodeRow:
    return CampEpisodeRow(
        tenant_id=tenant, run_id=run_id, episode_id=ep.episode_id,
        task_id=ep.task_id, agent_id=ep.agent_id, passed=ep.passed,
        score=ep.score, inputs=json.dumps(ep.inputs, ensure_ascii=False),
        action=json.dumps(ep.action, ensure_ascii=False),
        grade_detail=json.dumps(ep.grade_detail, ensure_ascii=False),
        system_prompt=ep.system_prompt,
    )


def _row_to_episode(row: CampEpisodeRow) -> Episode:
    return Episode(
        episode_id=row.episode_id, task_id=row.task_id, agent_id=row.agent_id,
        timestamp=0.0, inputs=json.loads(row.inputs), action=json.loads(row.action),
        passed=row.passed, score=row.score, grade_detail=json.loads(row.grade_detail),
        system_prompt=row.system_prompt,
    )


class CampStore:
    """Tenant-scoped CRUD for camp runs + episodes. Shares the Registry engine."""

    def __init__(self, engine, tenant: str = DEFAULT_TENANT):
        self.engine = engine
        self.tenant = tenant
        SQLModel.metadata.create_all(engine)  # idempotent

    # -- runs -----------------------------------------------------------------

    def create_run(self, run_id: str, *, kind: str, task_id: str, mode: str,
                   agent_label: str, threshold: float, min_episodes_for_gate: int,
                   seed: int) -> None:
        with Session(self.engine) as s:
            s.add(CampRunRow(
                tenant_id=self.tenant, run_id=run_id, kind=kind, task_id=task_id,
                mode=mode, agent_label=agent_label, threshold=threshold,
                min_episodes_for_gate=min_episodes_for_gate, seed=seed,
                created_at=_now()))
            s.commit()

    def finish_run(self, run_id: str, *, episodes: int, passes: int,
                   wilson_lower_95: float, pass_rate: float, report: dict,
                   gate: dict, rounds: list | None = None,
                   review_queue: list | None = None) -> None:
        with Session(self.engine) as s:
            row = self._row(s, run_id)
            row.status = "complete"
            row.finished_at = _now()
            row.episodes = episodes
            row.passes = passes
            row.wilson_lower_95 = wilson_lower_95
            row.pass_rate = pass_rate
            row.report = json.dumps(report, ensure_ascii=False)
            row.gate = json.dumps(gate, ensure_ascii=False)
            if rounds is not None:
                row.rounds = json.dumps(rounds, ensure_ascii=False)
            if review_queue is not None:
                row.review_queue = json.dumps(review_queue, ensure_ascii=False)
            s.add(row)
            s.commit()

    def fail_run(self, run_id: str, error: str) -> None:
        with Session(self.engine) as s:
            row = self._row(s, run_id)
            row.status = "error"
            row.error = error
            row.finished_at = _now()
            s.add(row)
            s.commit()

    def set_gate(self, run_id: str, gate: dict, *, approved_by: str | None,
                 approved_at: datetime | None) -> None:
        with Session(self.engine) as s:
            row = self._row(s, run_id)
            row.gate = json.dumps(gate, ensure_ascii=False)
            row.approved_by = approved_by
            row.approved_at = approved_at
            s.add(row)
            s.commit()

    def add_episodes(self, run_id: str, episodes: list[Episode]) -> None:
        if not episodes:
            return
        with Session(self.engine) as s:
            for ep in episodes:
                s.add(_episode_to_row(self.tenant, run_id, ep))
            s.commit()

    def _row(self, s: Session, run_id: str) -> CampRunRow:
        row = s.exec(select(CampRunRow).where(
            CampRunRow.tenant_id == self.tenant,
            CampRunRow.run_id == run_id)).first()
        if row is None:
            raise NotFoundError(f"camp run {run_id}")
        return row

    def get_run(self, run_id: str) -> dict:
        with Session(self.engine) as s:
            return self._run_dict(self._row(s, run_id))

    def get_run_row(self, run_id: str) -> CampRunRow:
        with Session(self.engine) as s:
            row = self._row(s, run_id)
            s.expunge(row)
            return row

    def list_runs(self) -> list[dict]:
        with Session(self.engine) as s:
            q = select(CampRunRow).where(
                CampRunRow.tenant_id == self.tenant).order_by(
                CampRunRow.created_at.desc())
            return [self._run_dict(r, full=False) for r in s.exec(q).all()]

    # -- episodes -------------------------------------------------------------

    def episodes(self, run_id: str, *, limit: int | None = None,
                 only_passing: bool | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(CampEpisodeRow).where(
                CampEpisodeRow.tenant_id == self.tenant,
                CampEpisodeRow.run_id == run_id).order_by(CampEpisodeRow.id)
            if only_passing is not None:
                q = q.where(CampEpisodeRow.passed == only_passing)
            if limit is not None:
                q = q.limit(limit)
            return [self._episode_dict(r) for r in s.exec(q).all()]

    def iter_episodes(self, run_id: str) -> Iterator[Episode]:
        with Session(self.engine) as s:
            q = select(CampEpisodeRow).where(
                CampEpisodeRow.tenant_id == self.tenant,
                CampEpisodeRow.run_id == run_id).order_by(CampEpisodeRow.id)
            for r in s.exec(q).all():
                yield _row_to_episode(r)

    def episode_count(self, run_id: str) -> int:
        with Session(self.engine) as s:
            q = select(CampEpisodeRow).where(
                CampEpisodeRow.tenant_id == self.tenant,
                CampEpisodeRow.run_id == run_id)
            return len(s.exec(q).all())

    # -- marshalling ----------------------------------------------------------

    def _run_dict(self, row: CampRunRow, full: bool = True) -> dict:
        out: dict[str, Any] = {
            "run_id": row.run_id,
            "kind": row.kind,
            "task_id": row.task_id,
            "agent_label": row.agent_label,
            "mode": row.mode,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "threshold": row.threshold,
            "min_episodes_for_gate": row.min_episodes_for_gate,
            "episodes": row.episodes,
            "passes": row.passes,
            "seed": row.seed,
            "pass_rate": row.pass_rate,
            "wilson_lower_95": row.wilson_lower_95,
            "gate": json.loads(row.gate),
            "approved_by": row.approved_by,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "error": row.error,
        }
        if full:
            out["report"] = json.loads(row.report)
            out["rounds"] = json.loads(row.rounds)
            out["review_queue"] = json.loads(row.review_queue)
        return out

    def _episode_dict(self, row: CampEpisodeRow) -> dict:
        return {
            "episode_id": row.episode_id,
            "agent_id": row.agent_id,
            "passed": row.passed,
            "score": row.score,
            "inputs": json.loads(row.inputs),
            "action": json.loads(row.action),
            "grade_detail": json.loads(row.grade_detail),
        }
