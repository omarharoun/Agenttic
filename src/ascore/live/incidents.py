"""Incident lifecycle FSM over the append-only ``incident_events`` stream
(SPEC-2 T16.1).

State is never stored mutably: it is *computed* by folding the event stream
(opened → triaged → reported → closed). The FSM rejects any transition not in
:data:`ascore.schema.incident.LEGAL_TRANSITIONS`, raising
:class:`IllegalTransitionError`.
"""

from __future__ import annotations

from ascore.registry.sqlite_store import NotFoundError
from ascore.schema.incident import LEGAL_TRANSITIONS, Incident

# event_type → resulting state (the lifecycle events; "note" is not a transition)
_EVENT_STATE = {
    "opened": "open",
    "triaged": "triaged",
    "reported": "reported",
    "closed": "closed",
}


class IllegalTransitionError(ValueError):
    """An incident transition that the lifecycle FSM forbids."""


class IncidentManager:
    """Thin FSM over a tenant's incident registry tables."""

    def __init__(self, reg):
        self.reg = reg

    # -- open -----------------------------------------------------------------

    def open(self, incident: Incident) -> Incident:
        """Open a new incident (persists the opening record + 'opened' event)."""
        self.reg.save_incident(incident)
        return incident

    # -- state (computed) -----------------------------------------------------

    def current_state(self, incident_id: str) -> str:
        events = self.reg.list_incident_events(incident_id)
        if not events:
            raise NotFoundError(f"incident {incident_id} has no events")
        state = "open"
        for e in events:
            new = _EVENT_STATE.get(e["event_type"])
            if new is not None:
                state = new
        return state

    def get(self, incident_id: str) -> Incident:
        """Reconstruct the incident with its *computed* current state + close
        time (from the event fold)."""
        incident = self.reg.get_incident_record(incident_id)
        events = self.reg.list_incident_events(incident_id)
        state = "open"
        closed_at = None
        for e in events:
            new = _EVENT_STATE.get(e["event_type"])
            if new is not None:
                state = new
                if new == "closed":
                    from datetime import datetime
                    closed_at = datetime.fromisoformat(e["created_at"])
        incident.state = state
        incident.closed_at = closed_at
        return incident

    # -- transition -----------------------------------------------------------

    def transition(self, incident_id: str, to_state: str, *, actor: str = "",
                   note: str = "") -> Incident:
        """Move an incident to ``to_state`` (triaged | reported | closed).
        Raises :class:`IllegalTransitionError` if the FSM forbids it."""
        current = self.current_state(incident_id)
        allowed = LEGAL_TRANSITIONS.get(current, ())
        if to_state not in allowed:
            raise IllegalTransitionError(
                f"incident {incident_id}: illegal transition {current} → "
                f"{to_state} (allowed: {list(allowed) or 'none — terminal'})")
        record = self.reg.get_incident_record(incident_id)
        self.reg.append_incident_event(
            incident_id, record.agent_id, event_type=to_state, actor=actor,
            note=note)
        return self.get(incident_id)

    def add_note(self, incident_id: str, note: str, *, actor: str = "") -> None:
        """Append a note event without changing state."""
        record = self.reg.get_incident_record(incident_id)
        self.reg.append_incident_event(
            incident_id, record.agent_id, event_type="note", actor=actor,
            note=note)


# --------------------------------------------------------------------------- #
# Triggers (T16.2): drift escalation, incident:sN-tagged live criteria, manual.
# --------------------------------------------------------------------------- #

import uuid as _uuid  # noqa: E402


def _new_incident_id() -> str:
    return f"inc-{_uuid.uuid4().hex[:10]}"


def severity_from_tag(tag: str) -> str | None:
    """Parse an ``incident:sN`` criterion tag into a severity ("S3"), or None."""
    t = (tag or "").strip().lower()
    if t.startswith("incident:s") and t[len("incident:s"):].isdigit():
        sev = "S" + t[len("incident:s"):]
        if sev in ("S1", "S2", "S3", "S4"):
            return sev
    return None


def open_from_drift(reg, cfg: dict, *, agent_id: str, reason: str,
                    trace_refs: list[str] | None = None) -> Incident:
    """Auto-open an incident from a drift escalation. Severity defaults to
    ``incidents.drift_default_severity`` (S3), with the drift trace refs
    attached and origin=drift."""
    sev = (cfg or {}).get("incidents", {}).get("drift_default_severity", "S3")
    inc = Incident(
        incident_id=_new_incident_id(), agent_id=agent_id, severity=sev,
        origin="drift", title="drift escalation",
        summary=reason, trace_refs=list(trace_refs or []))
    IncidentManager(reg).open(inc)
    return inc


def open_from_live_criterion(reg, *, agent_id: str, tag: str,
                             criterion_id: str = "",
                             trace_refs: list[str] | None = None) -> Incident | None:
    """Open an incident when an ``incident:sN``-tagged live criterion fires.
    Returns None if the tag isn't an incident tag."""
    sev = severity_from_tag(tag)
    if sev is None:
        return None
    inc = Incident(
        incident_id=_new_incident_id(), agent_id=agent_id, severity=sev,
        origin="live_criteria",
        title=f"live criterion {criterion_id or tag} fired",
        trace_refs=list(trace_refs or []))
    IncidentManager(reg).open(inc)
    return inc


def escalate_drift(reg, cfg: dict, status) -> Incident | None:
    """Given a :class:`~ascore.live.monitor.DriftStatus`, auto-open an S3
    incident if drift was detected. Returns the incident or None. Trace refs are
    the drifted criteria (the evidence pointer for the on-call reviewer)."""
    if not getattr(status, "drift_detected", False):
        return None
    drifted = getattr(status, "drifted", [])
    reason = ("live drift on criteria: " + ", ".join(drifted)) if drifted \
        else "live drift detected"
    return open_from_drift(reg, cfg, agent_id=status.agent_id, reason=reason,
                           trace_refs=[f"criterion:{c}" for c in drifted])


def open_manual(reg, *, agent_id: str, severity: str, title: str = "",
                summary: str = "", trace_refs: list[str] | None = None,
                actor: str = "manual") -> Incident:
    """Manually open an incident (CLI/API)."""
    inc = Incident(
        incident_id=_new_incident_id(), agent_id=agent_id, severity=severity,
        origin="manual", title=title, summary=summary,
        trace_refs=list(trace_refs or []))
    IncidentManager(reg).open(inc)
    return inc
