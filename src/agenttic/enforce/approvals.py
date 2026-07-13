"""Approval flow (SPEC-2 T26.2).

When a decision requires approval, the call is *parked* as an
:class:`ApprovalRequest`; a human resolves it (approve/deny) carrying their PAT
identity; the resolution is logged. If the approval **expires**, the outcome
follows the action class's fail policy (write ⇒ deny, read ⇒ allow).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from agenttic.schema.enforcement import ApprovalRequest, EnforcementEvent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def args_digest(args) -> str:
    payload = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class ApprovalManager:
    def __init__(self, reg, cfg: dict):
        self.reg = reg
        self.cfg = cfg or {}

    def _expiry_minutes(self) -> int:
        return int((self.cfg.get("enforcement", {})
                    .get("approvals", {}) or {}).get("default_expiry_minutes", 30))

    def park(self, session, decision, args, *, now: datetime | None = None
             ) -> ApprovalRequest:
        """Park a call awaiting approval."""
        now = now or _now()
        ar = ApprovalRequest(
            approval_id=f"appr-{uuid.uuid4().hex[:12]}",
            session_id=session.session_id, agent_id=session.agent_id,
            tool_name=decision.tool_name, action_class=decision.action_class,
            args_digest=args_digest(args), state="pending", created_at=now,
            expires_at=now + timedelta(minutes=self._expiry_minutes()))
        self.reg.save_approval(ar)
        self._log(session.session_id, session.agent_id, "approval", None,
                  {"approval_id": ar.approval_id, "event": "parked",
                   "tool": ar.tool_name, "action_class": ar.action_class})
        return ar

    def resolve(self, approval_id: str, approve: bool, resolver_identity: str,
                *, now: datetime | None = None) -> ApprovalRequest:
        """Resolve a pending approval, carrying the resolver's PAT identity."""
        ar = self.reg.get_approval(approval_id)
        if ar.state != "pending":
            return ar
        if ar.is_expired(now):
            return self.expire(approval_id, now=now)
        ar.state = "approved" if approve else "denied"
        ar.resolver_identity = resolver_identity
        ar.resolved_at = now or _now()
        self.reg.update_approval(ar)
        self._log(ar.session_id, ar.agent_id, "approval",
                  "allow" if approve else "deny",
                  {"approval_id": approval_id, "event": ar.state,
                   "resolver_identity": resolver_identity})
        return ar

    def expire(self, approval_id: str, *, now: datetime | None = None
               ) -> ApprovalRequest:
        """Expire a pending approval → outcome follows the class fail policy."""
        ar = self.reg.get_approval(approval_id)
        if ar.state != "pending":
            return ar
        ar.state = "expired"
        ar.resolved_at = now or _now()
        self.reg.update_approval(ar)
        self._log(ar.session_id, ar.agent_id, "approval",
                  self.effective_action(ar),
                  {"approval_id": approval_id, "event": "expired",
                   "fail_policy": self.effective_action(ar)})
        return ar

    def effective_action(self, ar: ApprovalRequest) -> str:
        """The action to enforce given the approval's state: approved ⇒ allow,
        denied ⇒ deny, expired ⇒ class fail policy (write ⇒ deny, read ⇒ allow),
        pending ⇒ require_approval (still parked)."""
        if ar.state == "approved":
            return "allow"
        if ar.state == "denied":
            return "deny"
        if ar.state == "expired":
            policy = (self.cfg.get("enforcement", {}) or {}).get("fail_policy", {})
            return "allow" if policy.get(ar.action_class) == "open" else "deny"
        return "require_approval"

    def sweep_expired(self, *, now: datetime | None = None) -> list[str]:
        """Expire all pending approvals past their deadline."""
        expired = []
        for row in self.reg.list_approvals(state="pending"):
            ar = ApprovalRequest.model_validate(row)
            if ar.is_expired(now):
                self.expire(ar.approval_id, now=now)
                expired.append(ar.approval_id)
        return expired

    def _log(self, session_id, agent_id, kind, action, detail) -> None:
        self.reg.append_enforcement_event(EnforcementEvent(
            event_id=f"evt-{uuid.uuid4().hex[:12]}", session_id=session_id,
            agent_id=agent_id, kind=kind, action=action, actor="approvals",
            detail=detail))
