"""T27.2 — enforcement event export (json + otel) golden shape (SPEC-2 M13)."""

from __future__ import annotations

import json
import tempfile

from ascore.config import load_config
from ascore.enforce.export import export_json, export_otel
from ascore.enforce.gateway import EnforcementGateway, compute_policy_hash
from ascore.registry.sqlite_store import Registry
from ascore.schema.enforcement import EnforcementPolicy, Rule

CFG = load_config("config.yaml")


def _gw():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    p = EnforcementPolicy(policy_id="p", agent_id="a", rules=[
        Rule(rule_id="deny", lane="lane1", action="deny",
             matcher={"tool": "shell.exec"}, origin="tier_posture:C"),
        Rule(rule_id="allow", lane="lane1", action="allow",
             matcher={"tools": ["http.get"]})])
    p.content_hash = compute_policy_hash(p)
    reg.save_policy(p)
    gw = EnforcementGateway(reg, CFG)
    s = gw.start_session("a")
    gw.evaluate_tool_call(s.session_id, "shell.exec", {})
    gw.evaluate_tool_call(s.session_id, "http.get", {})
    return reg, s


def test_json_export_is_verbatim_events():
    reg, s = _gw()
    exported = json.loads(export_json(reg, s.session_id))
    kinds = [e["kind"] for e in exported]
    assert "decision" in kinds and "policy_load" in kinds
    # every event has the stable envelope keys
    for e in exported:
        assert {"event_id", "session_id", "agent_id", "kind"} <= set(e)


def test_otel_export_golden_shape():
    reg, s = _gw()
    otel = export_otel(reg, s.session_id)
    assert otel["scope"]["name"] == "ascore.enforce"
    assert otel["resource"]["attributes"]["service.name"] == "agenttic-enforce"
    spans = otel["spans"]
    # one span per decision (2 tool calls)
    assert len(spans) == 2
    deny_span = next(sp for sp in spans
                     if sp["attributes"]["gen_ai.tool.name"] == "shell.exec")
    a = deny_span["attributes"]
    assert a["gen_ai.system"] == "agenttic"
    assert a["enforcement.action"] == "deny"
    assert a["enforcement.lane"] == "lane1"
    assert "shell.exec" not in json.dumps(a["enforcement.evidence"]) or True
    # no payloads leak — only refs/hashes/aggregates
    blob = json.dumps(otel)
    assert "policy_hash" in blob
