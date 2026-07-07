"""Enforcement contracts (SPEC-2 M11, T23.1).

The enforcement gateway sits inline on an agent's tool calls and results. Its
data model:

* :class:`Rule` — one policy rule: which lane it runs in, what it matches, and
  the action to take (closed vocabulary), plus the origin that produced it.
* :class:`EnforcementPolicy` — a compiled, content-hashed set of rules with refs
  to the evidence it was compiled from (dossier/card/incidents).
* :class:`Decision` — the outcome of evaluating one tool call/result: action,
  latency, the evidence (which rule fired), and a ref to the preserved original
  when a value was transformed/quarantined.
* :class:`EnforcementEvent` — the single append-only log covering BOTH agent
  decisions AND admin/judge actions (Hard Rule 19: no unlogged enforcement).
* :class:`ApprovalRequest` — a parked call awaiting human approval.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# Closed action vocabulary — a rule may only ever take one of these.
Action = Literal[
    "allow", "transform", "require_approval", "deny",
    "terminate_session", "revoke_access",
]
Lane = Literal["lane1", "lane2", "lane3"]
ActionClass = Literal["read", "write", "unknown"]
Phase = Literal["tool_call", "tool_result"]


class Rule(BaseModel):
    """One enforcement rule. ``matcher`` is a small declarative dict interpreted
    by the lane (tool name, arg patterns, egress host, action class, …).
    ``origin`` names the mapping/evidence that produced the rule (e.g.
    ``tier_posture:B``, ``cap:elicitation_gap:tool_use``)."""

    rule_id: str
    lane: Lane
    action: Action
    matcher: dict = Field(default_factory=dict)
    origin: str = "manual"
    description: str = ""

    def ref(self) -> str:
        return f"rule:{self.rule_id}"


class EnforcementPolicy(BaseModel):
    """A compiled policy — an ordered list of rules with a content hash and refs
    to the evidence it was compiled from."""

    policy_id: str
    agent_id: str
    rules: list[Rule] = Field(default_factory=list)
    compiled_from: list[str] = Field(default_factory=list)  # dossier/card/incident refs
    content_hash: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def hashable_content(self) -> dict:
        data = self.model_dump(mode="json")
        data.pop("content_hash", None)
        data.pop("created_at", None)
        return data

    def ref(self) -> str:
        return f"policy:{self.policy_id}"


class Decision(BaseModel):
    """The outcome of evaluating one tool call/result."""

    decision_id: str
    session_id: str
    agent_id: str
    phase: Phase
    action: Action
    lane: Lane
    tool_name: str = ""
    action_class: ActionClass = "unknown"
    latency_ms: float = 0.0
    evidence: list[str] = Field(default_factory=list)   # rule refs / patterns that fired
    fail_open: bool = False                              # set when a fail-open occurred
    original_preserved_ref: str | None = None            # ref to preserved original on transform
    policy_hash: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def ref(self) -> str:
        return f"decision:{self.decision_id}"


class EnforcementEvent(BaseModel):
    """The single append-only enforcement log entry. ``kind`` distinguishes an
    agent decision from an admin/judge/system action; both live in the same log
    (Hard Rule 19)."""

    event_id: str
    session_id: str
    agent_id: str
    kind: str  # decision | admin | judge | approval | policy_load | refusal | receipt
    action: Action | None = None
    actor: str = ""            # who/what produced it (system, judge, an admin email)
    decision_ref: str | None = None
    policy_hash: str = ""
    detail: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ApprovalRequest(BaseModel):
    """A tool call parked awaiting human approval."""

    approval_id: str
    session_id: str
    agent_id: str
    tool_name: str
    action_class: ActionClass = "write"
    args_digest: str = ""                  # hash of the args (no raw payload by default)
    state: Literal["pending", "approved", "denied", "expired"] = "pending"
    resolver_identity: str | None = None   # PAT identity of the approver
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    resolved_at: datetime | None = None

    @model_validator(mode="after")
    def _tz(self) -> "ApprovalRequest":
        for attr in ("created_at", "expires_at", "resolved_at"):
            v = getattr(self, attr)
            if v is not None and v.tzinfo is None:
                setattr(self, attr, v.replace(tzinfo=timezone.utc))
        return self

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.state != "pending" or self.expires_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now > self.expires_at
