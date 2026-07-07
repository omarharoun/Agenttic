"""T16.5 — incident export golden schema (SPEC-2 M6)."""

from __future__ import annotations

from datetime import datetime, timezone

from ascore.schema.incident import Incident

CFG = {"incidents": {"sla_hours": {"S1": 72, "S2": 72, "S3": 168, "S4": 336}}}

GOLDEN_KEYS = {
    "schema", "incident_id", "affected_system", "severity", "status", "summary",
    "title", "origin", "discovered_at", "sla_due_at", "closed_at",
    "evidence_refs", "trace_refs",
}


def test_export_matches_golden_schema():
    inc = Incident(
        incident_id="inc-1", agent_id="ref-agent", severity="S2",
        title="drift", summary="live drift on harm-refusal",
        origin="drift", trace_refs=["criterion:harm_refusal"],
        opened_at=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc))
    exp = inc.export(CFG)
    assert set(exp) == GOLDEN_KEYS
    assert exp["schema"] == "agenttic-incident-export/v1"
    assert exp["affected_system"] == "ref-agent"
    assert exp["severity"] == "S2"
    assert exp["status"] == "open"
    # discovered + 72h = sla_due, in UTC ISO
    assert exp["discovered_at"] == "2026-01-02T03:04:00+00:00"
    assert exp["sla_due_at"] == "2026-01-05T03:04:00+00:00"
    assert exp["closed_at"] is None


def test_export_has_no_payload_fields():
    inc = Incident(incident_id="i", agent_id="a", severity="S1")
    exp = inc.export(CFG)
    # no raw payloads / PII by default (Hard Rule 30)
    for forbidden in ("payload", "input", "output", "pii", "content"):
        assert forbidden not in exp
