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
