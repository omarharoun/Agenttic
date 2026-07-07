"""Enforcement event export (SPEC-2 T27.2) — JSON + OTel-GenAI.

* ``export_json`` — the append-only events as a JSON-safe list (verbatim).
* ``export_otel`` — the decisions as OpenTelemetry GenAI-style spans, so an
  enforcement trace drops into an OTel/GenAI pipeline. Only hashes + aggregates
  leave; no payloads (Hard Rule 30).
"""

from __future__ import annotations

import json

OTEL_SCHEMA = "https://opentelemetry.io/schemas/1.28.0"


def export_json(reg, session_id: str | None = None,
                agent_id: str | None = None, *, redact: bool = True) -> str:
    events = reg.list_enforcement_events(session_id, agent_id)
    if redact:
        from ascore.enforce.self_security import redact_events
        events = redact_events(events)
    return json.dumps(events, sort_keys=True)


def export_otel(reg, session_id: str | None = None,
                agent_id: str | None = None) -> dict:
    """Decisions as OTel-GenAI spans. Each span carries gen_ai.* + enforcement.*
    attributes (no payloads)."""
    events = reg.list_enforcement_events(session_id, agent_id)
    spans = []
    for e in events:
        if e.get("kind") != "decision":
            continue
        detail = e.get("detail") or {}
        spans.append({
            "name": f"enforce.{detail.get('phase', 'tool_call')}",
            "kind": "SPAN_KIND_INTERNAL",
            "attributes": {
                "gen_ai.system": "agenttic",
                "gen_ai.operation.name": "enforce_tool_call",
                "gen_ai.tool.name": detail.get("tool", ""),
                "enforcement.action": e.get("action"),
                "enforcement.lane": detail.get("lane"),
                "enforcement.action_class": detail.get("action_class"),
                "enforcement.fail_open": bool(detail.get("fail_open")),
                "enforcement.policy_hash": e.get("policy_hash", ""),
                "enforcement.decision_ref": e.get("decision_ref"),
                "enforcement.evidence": detail.get("evidence", []),
            },
        })
    return {
        "schema_url": OTEL_SCHEMA,
        "resource": {"attributes": {"service.name": "agenttic-enforce"}},
        "scope": {"name": "ascore.enforce", "version": "1"},
        "spans": spans,
    }
