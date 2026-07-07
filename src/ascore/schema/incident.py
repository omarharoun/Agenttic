"""Incident schema (SPEC-2 M6).

An incident is a safety event with a severity (S1–S4), a lifecycle
(open → triaged → reported → closed), and an SLA clock read from
``config.incidents.sla_hours``. Incidents are stored append-only as events; the
current state is computed from the event stream (``live/incidents.py``). This
module holds the pure data contract + the SLA-clock helper.

All timestamps are timezone-aware (UTC). ``sla_due`` returns a tz-aware datetime,
so SLA math is correct across timezones and DST transitions (the clock is an
absolute UTC offset from ``opened_at``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Severity = Literal["S1", "S2", "S3", "S4"]
IncidentState = Literal["open", "triaged", "reported", "closed"]

# Legal forward transitions. State is computed from the append-only event log;
# the FSM (live/incidents.py) rejects anything not listed here.
LEGAL_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "open": ("triaged", "closed"),
    "triaged": ("reported", "closed"),
    "reported": ("closed",),
    "closed": (),
}

# Ordering used to compare / escalate severities (S1 most severe).
SEVERITY_ORDER = {"S1": 0, "S2": 1, "S3": 2, "S4": 3}


def default_sla_hours() -> dict[str, int]:
    """Fallback SLA table if config is unavailable (mirrors config.yaml)."""
    return {"S1": 72, "S2": 72, "S3": 168, "S4": 336}


def sla_hours_from_config(cfg: dict | None) -> dict[str, int]:
    if not cfg:
        return default_sla_hours()
    return dict(cfg.get("incidents", {}).get("sla_hours", default_sla_hours()))


class Incident(BaseModel):
    """A safety incident. Severity + lifecycle state + SLA clock.

    The model is a snapshot; the source of truth is the append-only
    ``incident_events`` stream, from which state is recomputed."""

    incident_id: str
    agent_id: str
    severity: Severity
    state: IncidentState = "open"
    title: str = ""
    summary: str = ""
    trace_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    origin: str = "manual"  # manual | drift | live_criteria | canary
    opened_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    closed_at: datetime | None = None

    @model_validator(mode="after")
    def _tz_aware(self) -> "Incident":
        if self.opened_at.tzinfo is None:
            self.opened_at = self.opened_at.replace(tzinfo=timezone.utc)
        if self.closed_at is not None and self.closed_at.tzinfo is None:
            self.closed_at = self.closed_at.replace(tzinfo=timezone.utc)
        return self

    def sla_due(self, cfg: dict | None = None) -> datetime:
        """The tz-aware UTC deadline for this incident, ``opened_at`` plus the
        configured SLA hours for its severity."""
        hours = sla_hours_from_config(cfg).get(
            self.severity, default_sla_hours()[self.severity]
        )
        return self.opened_at + timedelta(hours=int(hours))

    def is_overdue(self, cfg: dict | None = None, now: datetime | None = None) -> bool:
        """True if the incident is still open past its SLA deadline. Closed
        incidents are never overdue."""
        if self.state == "closed":
            return False
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now > self.sla_due(cfg)

    def ref(self) -> str:
        return f"incident:{self.incident_id}"
