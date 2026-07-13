"""Tenant-scoped persistence for Copilot AGENT sessions.

Thin wrapper over :class:`agenttic.registry.sqlite_store.CopilotSessionRow`. The
full agent state (Anthropic transcript with tool_use/tool_result blocks, step
log, any write-action awaiting confirmation) lives in the ``payload`` JSON;
``status`` is mirrored to a column so a resumed request can tell "is this session
waiting on my confirmation?" without parsing the payload. Every read/write is
scoped to ``tenant`` — one tenant never sees another's Copilot sessions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from agenttic.registry.sqlite_store import CopilotSessionRow, NotFoundError


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CopilotStore:
    def __init__(self, engine, tenant: str = "default"):
        self.engine = engine
        self.tenant = tenant

    def save(self, session: dict) -> None:
        sid = session["session_id"]
        status = session.get("status", "ready")
        payload = json.dumps(session)
        with Session(self.engine) as s:
            row = s.exec(select(CopilotSessionRow).where(
                CopilotSessionRow.tenant_id == self.tenant,
                CopilotSessionRow.session_id == sid)).first()
            if row is None:
                s.add(CopilotSessionRow(
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
            row = s.exec(select(CopilotSessionRow).where(
                CopilotSessionRow.tenant_id == self.tenant,
                CopilotSessionRow.session_id == session_id)).first()
        if row is None:
            raise NotFoundError(f"copilot session {session_id}")
        return json.loads(row.payload)
