"""T16.4 — incident SLA clocks + overdue flag over the manager, tz/DST aware."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ascore.config import load_config
from ascore.live.incidents import IncidentManager, open_manual
from ascore.registry.sqlite_store import Registry


def _reg():
    return Registry(db_path=tempfile.mktemp(suffix=".db"))


def test_s1_s2_default_72h_overdue():
    cfg = load_config("config.yaml")
    reg = _reg()
    m = IncidentManager(reg)
    inc = open_manual(reg, agent_id="a1", severity="S2", title="t")
    # freeze the opened_at for deterministic math by reading it back
    opened = m.get(inc.incident_id).opened_at
    assert not m.is_overdue(inc.incident_id, cfg, now=opened + timedelta(hours=71))
    assert m.is_overdue(inc.incident_id, cfg, now=opened + timedelta(hours=73))


def test_sla_absolute_across_dst():
    cfg = load_config("config.yaml")
    reg = _reg()
    m = IncidentManager(reg)
    # open with a DST-boundary timezone
    from ascore.schema.incident import Incident
    opened = datetime(2025, 3, 9, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    inc = Incident(incident_id="i-dst", agent_id="a1", severity="S1",
                   opened_at=opened)
    m.open(inc)
    due = m.sla_due("i-dst", cfg)
    # SLA is an ABSOLUTE 72 real hours from the opening instant (DST-immune) —
    # compare as instants in UTC, since persistence normalizes to a fixed offset.
    assert due.astimezone(timezone.utc) == \
        opened.astimezone(timezone.utc) + timedelta(hours=72)


def test_closed_incident_not_overdue_and_listing():
    cfg = load_config("config.yaml")
    reg = _reg()
    m = IncidentManager(reg)
    inc = open_manual(reg, agent_id="a1", severity="S1", title="t")
    m.transition(inc.incident_id, "triaged")
    m.transition(inc.incident_id, "closed")
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert not m.is_overdue(inc.incident_id, cfg, now=far_future)
    rows = m.list_with_sla(cfg, now=far_future)
    row = rows[0]
    assert row["state"] == "closed" and row["overdue"] is False
    assert "sla_due" in row
