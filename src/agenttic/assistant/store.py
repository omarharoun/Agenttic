"""Tenant-scoped persistence for Safe Reference Assistant sessions.

Thin wrapper over :class:`agenttic.registry.sqlite_store.AssistantSessionRow`. A
session's full state (transcript, scratchpad, step log, pending approval) lives
in the ``payload`` JSON; ``status`` is mirrored to a column so the API can list
sessions and answer "is this one waiting on me?" without parsing every payload.
Every read/write is scoped to ``tenant`` — one tenant never sees another's
conversations.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from agenttic.registry.sqlite_store import AssistantSessionRow, NotFoundError


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AssistantStore:
    def __init__(self, engine, tenant: str = "default"):
        self.engine = engine
        self.tenant = tenant

    def save(self, session: dict) -> None:
        """Upsert the session by (tenant, session_id)."""
        sid = session["session_id"]
        status = session.get("status", "ready")
        payload = json.dumps(session)
        with Session(self.engine) as s:
            row = s.exec(select(AssistantSessionRow).where(
                AssistantSessionRow.tenant_id == self.tenant,
                AssistantSessionRow.session_id == sid)).first()
            if row is None:
                s.add(AssistantSessionRow(
                    tenant_id=self.tenant, session_id=sid, status=status,
                    payload=payload, created_at=_now(), updated_at=_now()))
            else:
                row.status = status
                row.payload = payload
                row.updated_at = _now()
                s.add(row)
            s.commit()

    def get(self, session_id: str) -> dict:
        with Session(self.engine) as s:
            row = s.exec(select(AssistantSessionRow).where(
                AssistantSessionRow.tenant_id == self.tenant,
                AssistantSessionRow.session_id == session_id)).first()
        if row is None:
            raise NotFoundError(f"assistant session {session_id}")
        return json.loads(row.payload)

    def list(self) -> list[dict]:
        """Newest-first session summaries (no transcript) for this tenant."""
        with Session(self.engine) as s:
            rows = s.exec(select(AssistantSessionRow).where(
                AssistantSessionRow.tenant_id == self.tenant)
                .order_by(AssistantSessionRow.updated_at.desc())).all()
        return [{"session_id": r.session_id, "status": r.status,
                 "created_at": r.created_at.isoformat(),
                 "updated_at": r.updated_at.isoformat()} for r in rows]
