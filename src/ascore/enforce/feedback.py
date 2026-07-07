"""Enforcement → hardening feedback (SPEC-2 T26.4).

Every deny/quarantine is logged as a *hardening candidate* by the gateway. When a
human reviewer marks a flagged decision **benign** (a false positive), it becomes
a *checker-eval case* — a seed for evaluating (and tuning down the false-positive
rate of) the classifier that flagged it.
"""

from __future__ import annotations

import uuid

from ascore.schema.enforcement import EnforcementEvent


def hardening_candidates(reg, session_id: str | None = None,
                         agent_id: str | None = None) -> list[dict]:
    """All hardening-candidate events (deny/quarantine) for a session/agent."""
    events = reg.list_enforcement_events(session_id, agent_id)
    return [e for e in events
            if e.get("kind") == "admin"
            and "hardening_candidate" in (e.get("detail") or {})]


def mark_false_positive(reg, session_id: str, agent_id: str, decision_ref: str,
                        reviewer: str, note: str = "") -> str:
    """A reviewer marks a flagged decision benign → emit a checker-eval case
    (an evaluation seed to reduce classifier false positives). Returns the case
    event id."""
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    reg.append_enforcement_event(EnforcementEvent(
        event_id=event_id, session_id=session_id, agent_id=agent_id,
        kind="admin", actor=reviewer, decision_ref=decision_ref,
        detail={"checker_eval_case": True, "verdict": "benign",
                "reviewer": reviewer, "note": note}))
    return event_id


def checker_eval_cases(reg, agent_id: str | None = None) -> list[dict]:
    """Reviewer-benign checker-eval cases (false positives to learn from)."""
    events = reg.list_enforcement_events(None, agent_id)
    return [e for e in events
            if e.get("kind") == "admin"
            and (e.get("detail") or {}).get("checker_eval_case")]
