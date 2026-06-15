"""UI persistence: workflows, executions, execution events — plus the
list-all browse queries the Registry deliberately lacks.

Shares the Registry's SQLite engine (one file, WAL mode for concurrent
event writes from worker threads while the API reads). The Registry itself
is untouched: its append-only contract stays exactly as specified.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint, text
from sqlmodel import Field, Session, SQLModel, select

from ascore.registry.sqlite_store import (
    NotFoundError,
    RubricRow,
    ScorecardRow,
    SuiteRow,
    TraceRow,
)
from ascore.server.workflow_schema import Workflow


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    workflow_id: str = Field(index=True, unique=True)
    name: str
    updated_at: datetime
    payload: str  # Workflow JSON


class ExecutionRow(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    execution_id: str = Field(index=True, unique=True)
    workflow_id: str = Field(index=True)
    status: str  # running|waiting_approval|succeeded|failed|cancelled|interrupted
    waiting_node_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    workflow_snapshot: str  # full Workflow JSON frozen at start (reproducibility)
    node_states: str = "{}"   # {node_id: pending|running|waiting|succeeded|failed|skipped}
    node_outputs: str = "{}"  # {node_id: {port: payload}} — enables gate resume


class ExecutionEventRow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("execution_id", "seq"),)
    id: int | None = Field(default=None, primary_key=True)
    execution_id: str = Field(index=True)
    seq: int
    ts: datetime
    type: str
    node_id: str | None = None
    data: str = "{}"


class UIStore:
    """Wraps the SAME engine as the Registry; create_all is idempotent."""

    def __init__(self, engine):
        self.engine = engine
        SQLModel.metadata.create_all(engine)
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))

    # -- workflows ----------------------------------------------------------

    def save_workflow(self, wf: Workflow) -> None:
        with Session(self.engine) as s:
            row = s.exec(select(WorkflowRow).where(
                WorkflowRow.workflow_id == wf.workflow_id)).first()
            if row is None:
                row = WorkflowRow(workflow_id=wf.workflow_id, name=wf.name,
                                  updated_at=_now(), payload=wf.model_dump_json())
            else:
                row.name, row.updated_at = wf.name, _now()
                row.payload = wf.model_dump_json()
            s.add(row)
            s.commit()

    def get_workflow(self, workflow_id: str) -> Workflow:
        with Session(self.engine) as s:
            row = s.exec(select(WorkflowRow).where(
                WorkflowRow.workflow_id == workflow_id)).first()
            if row is None:
                raise NotFoundError(f"workflow {workflow_id}")
            return Workflow.model_validate_json(row.payload)

    def list_workflows(self) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.exec(select(WorkflowRow).order_by(
                WorkflowRow.updated_at.desc())).all()
            out = []
            for r in rows:
                wf = Workflow.model_validate_json(r.payload)
                out.append({"workflow_id": r.workflow_id, "name": r.name,
                            "updated_at": r.updated_at.isoformat(),
                            "n_nodes": len(wf.nodes)})
            return out

    def delete_workflow(self, workflow_id: str) -> None:
        with Session(self.engine) as s:
            row = s.exec(select(WorkflowRow).where(
                WorkflowRow.workflow_id == workflow_id)).first()
            if row:
                s.delete(row)
                s.commit()

    # -- executions ---------------------------------------------------------

    def create_execution(self, execution_id: str, wf: Workflow) -> None:
        states = {n.node_id: "pending" for n in wf.nodes}
        with Session(self.engine) as s:
            s.add(ExecutionRow(
                execution_id=execution_id, workflow_id=wf.workflow_id,
                status="running", started_at=_now(),
                workflow_snapshot=wf.model_dump_json(),
                node_states=json.dumps(states)))
            s.commit()

    def update_execution(self, execution_id: str, *, status: str | None = None,
                         waiting_node_id: str | None | bool = False,
                         node_state: tuple[str, str] | None = None,
                         node_output: tuple[str, dict] | None = None,
                         finished: bool = False) -> None:
        with Session(self.engine) as s:
            row = s.exec(select(ExecutionRow).where(
                ExecutionRow.execution_id == execution_id)).first()
            if row is None:
                raise NotFoundError(f"execution {execution_id}")
            if status is not None:
                row.status = status
            if waiting_node_id is not False:
                row.waiting_node_id = waiting_node_id
            if node_state is not None:
                states = json.loads(row.node_states)
                states[node_state[0]] = node_state[1]
                row.node_states = json.dumps(states)
            if node_output is not None:
                outputs = json.loads(row.node_outputs)
                outputs[node_output[0]] = node_output[1]
                row.node_outputs = json.dumps(outputs)
            if finished:
                row.finished_at = _now()
            s.add(row)
            s.commit()

    def get_execution(self, execution_id: str) -> dict:
        with Session(self.engine) as s:
            row = s.exec(select(ExecutionRow).where(
                ExecutionRow.execution_id == execution_id)).first()
            if row is None:
                raise NotFoundError(f"execution {execution_id}")
            return self._execution_dict(row, full=True)

    def list_executions(self, workflow_id: str | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(ExecutionRow).order_by(ExecutionRow.started_at.desc())
            if workflow_id:
                q = q.where(ExecutionRow.workflow_id == workflow_id)
            return [self._execution_dict(r, full=False) for r in s.exec(q).all()]

    @staticmethod
    def _execution_dict(row: ExecutionRow, *, full: bool) -> dict:
        d = {
            "execution_id": row.execution_id, "workflow_id": row.workflow_id,
            "status": row.status, "waiting_node_id": row.waiting_node_id,
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "node_states": json.loads(row.node_states),
        }
        if full:
            d["node_outputs"] = json.loads(row.node_outputs)
            d["workflow"] = json.loads(row.workflow_snapshot)
        return d

    def interrupt_orphans(self) -> int:
        """Startup hygiene: anything still 'running' did not survive the
        previous process. waiting_approval rows stay resumable."""
        with Session(self.engine) as s:
            rows = s.exec(select(ExecutionRow).where(
                ExecutionRow.status == "running")).all()
            for r in rows:
                r.status = "interrupted"
                r.finished_at = _now()
                s.add(r)
            s.commit()
            return len(rows)

    # -- execution events ---------------------------------------------------

    def append_event(self, execution_id: str, seq: int, etype: str,
                     node_id: str | None, data: dict) -> None:
        with Session(self.engine) as s:
            s.add(ExecutionEventRow(execution_id=execution_id, seq=seq,
                                    ts=_now(), type=etype, node_id=node_id,
                                    data=json.dumps(data)))
            s.commit()

    def events_after(self, execution_id: str, after: int = 0) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.exec(select(ExecutionEventRow)
                          .where(ExecutionEventRow.execution_id == execution_id,
                                 ExecutionEventRow.seq > after)
                          .order_by(ExecutionEventRow.seq)).all()
            return [{"seq": r.seq, "ts": r.ts.isoformat(), "type": r.type,
                     "node_id": r.node_id, "data": json.loads(r.data)}
                    for r in rows]

    def last_seq(self, execution_id: str) -> int:
        events = self.events_after(execution_id, 0)
        return events[-1]["seq"] if events else 0

    # -- registry browse queries (list-alls the Registry lacks) -------------

    def list_suites(self) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.exec(select(SuiteRow)).all()
            latest: dict[str, SuiteRow] = {}
            for r in rows:
                if r.suite_id not in latest or r.version > latest[r.suite_id].version:
                    latest[r.suite_id] = r
            out = []
            for r in sorted(latest.values(), key=lambda r: r.suite_id):
                payload = json.loads(r.payload)
                out.append({"suite_id": r.suite_id, "version": r.version,
                            "approved": r.approved,
                            "n_cases": len(payload.get("test_ids", [])),
                            "business_context": payload.get("business_context", "")})
            return out

    def list_rubrics(self) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.exec(select(RubricRow)).all()
            latest: dict[str, RubricRow] = {}
            for r in rows:
                if r.rubric_id not in latest or r.version > latest[r.rubric_id].version:
                    latest[r.rubric_id] = r
            return [{"rubric_id": r.rubric_id, "version": r.version,
                     "n_criteria": len(json.loads(r.payload).get("criteria", []))}
                    for r in sorted(latest.values(), key=lambda r: r.rubric_id)]

    def list_traces(self, agent_id: str | None = None, mode: str | None = None,
                    limit: int = 50, offset: int = 0) -> list[dict]:
        with Session(self.engine) as s:
            q = select(TraceRow).order_by(TraceRow.id.desc())
            if agent_id:
                q = q.where(TraceRow.agent_id == agent_id)
            if mode:
                q = q.where(TraceRow.mode == mode)
            rows = s.exec(q.offset(offset).limit(limit)).all()
            out = []
            for r in rows:
                p = json.loads(r.payload)
                out.append({"trace_id": r.trace_id, "agent_id": r.agent_id,
                            "mode": r.mode, "test_case_id": p.get("test_case_id"),
                            "final_output": (p.get("final_output") or "")[:200],
                            "total_steps": p.get("total_steps"),
                            "total_cost_usd": p.get("total_cost_usd"),
                            "n_spans": len(p.get("spans", []))})
            return out

    def list_scorecards(self, agent_id: str | None = None,
                        suite_id: str | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(ScorecardRow).order_by(ScorecardRow.created_at.desc())
            if agent_id:
                q = q.where(ScorecardRow.agent_id == agent_id)
            if suite_id:
                q = q.where(ScorecardRow.suite_id == suite_id)
            out = []
            for r in s.exec(q).all():
                p = json.loads(r.payload)
                out.append({"scorecard_id": r.scorecard_id, "agent_id": r.agent_id,
                            "suite_id": r.suite_id, "suite_version": r.suite_version,
                            "task_success_rate": p.get("task_success_rate"),
                            "mean_cost_usd": p.get("mean_cost_usd"),
                            "p95_latency_ms": p.get("p95_latency_ms"),
                            "n_errored": len(p.get("errored_test_ids", [])),
                            "visibility_tier": p.get("visibility_tier"),
                            "created_at": r.created_at.isoformat()})
            return out
