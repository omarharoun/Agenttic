"""Fail-CLOSED regression tests for the enforcement gateway (SPEC-2 M11/M15).

The one bug class an enforcement product can't ship: the block path swallowing an
error and falling THROUGH to allow. A confirmed canary/decoy trip (or a stage-gate
violation) whose incident-open or decision-log raises must still DENY the call —
never silently let the planted forbidden tool through.

Covers:
  (a) canary trip + incident-open raises → Decision is DENY, tool NOT executed;
  (b) honeypot enforce-posture end-to-end blocks even when the incident path errors;
  (c) controls — a normal allowed call still allows, a normal (policy) deny still
      denies, and a genuinely-unrelated non-enforcement error (canary *detection*
      failure on an allowed call) is NOT misclassified into a deny;
  (d) the fail-closed deny is surfaced (telemetry + append-only event), not swallowed.
"""

from __future__ import annotations

import tempfile

import pytest

from agenttic.enforce.canaries import CanaryManager
from agenttic.enforce.gateway import EnforcementGateway, compute_policy_hash
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.enforcement import EnforcementPolicy, Rule
from agenttic.server import metrics


def _boom(msg="incident store unavailable"):
    def _raise(*_a, **_k):
        raise RuntimeError(msg)
    return _raise


def _reg_with_policy(rules=None):
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    p = EnforcementPolicy(policy_id="p", agent_id="a", rules=rules or [])
    p.content_hash = compute_policy_hash(p)
    reg.save_policy(p)
    return reg


def _enforcing_setup(rules=None, decoys=("delete_all_records",)):
    reg = _reg_with_policy(rules)
    CanaryManager(reg, {}).install("a", decoy_tools=list(decoys))
    gw = EnforcementGateway(reg, {})
    return reg, gw, gw.start_session("a")


# -- (a) canary trip + incident-open raises → DENY, not executed ------------- #

def test_canary_trip_fails_closed_when_incident_open_raises(monkeypatch):
    reg, gw, s = _enforcing_setup()
    monkeypatch.setattr("agenttic.live.incidents.open_manual", _boom())
    d = gw.evaluate_tool_call(s.session_id, "delete_all_records", {"confirm": "y"})
    # the exact fail-open path: incident handling raised, yet the decoy is DENIED
    assert d.action == "deny"
    assert d.lane == "lane1"
    assert any(e.startswith("canary:") for e in d.evidence)
    assert "incident:unavailable" in d.evidence
    assert any(e.startswith("canary_handler_error:") for e in d.evidence)


def test_guarded_adapter_does_not_execute_decoy_on_incident_error(monkeypatch,
                                                                  tmp_path):
    """Through the real inline-gateway adapter: the decoy tool must NOT execute
    even when the incident store is down."""
    from agenttic.redteam import (
        build_guarded_demo_target,
        install_honeypot_enforcement,
        plant_honeypots,
        reference_descriptor,
    )
    monkeypatch.setattr("agenttic.live.incidents.open_manual", _boom())
    planted = plant_honeypots(reference_descriptor())
    names = planted.honeypot_tool_names()
    reg = Registry(str(tmp_path / "g.db"))
    gw, sess = install_honeypot_enforcement(reg, planted.agent_id, names,
                                            enforcing=True)
    adapter = build_guarded_demo_target(planted, gw, sess.session_id,
                                        kb_path=str(tmp_path / "kb.json"))
    result, err = adapter._exec_tool(names[0], {"confirm": "yes"})
    assert result is None                       # the dangerous call did NOT run
    assert "BLOCKED_BY_HARNESS" in (err or "")   # blocked inline
    assert adapter._decisions[-1].action == "deny"


# -- (b) honeypot enforce-posture end-to-end still blocks under incident error #

def test_honeypot_harness_blocks_even_when_incident_path_errors(monkeypatch,
                                                                tmp_path):
    from agenttic.redteam import (
        ATTEMPTED_BLOCKED,
        EXECUTED_ALLOWED,
        plant_honeypots,
        reference_descriptor,
        run_honeypot_harness,
    )
    monkeypatch.setattr("agenttic.live.incidents.open_manual", _boom())
    planted = plant_honeypots(reference_descriptor())
    reg = Registry(str(tmp_path / "e.db"))
    rep = run_honeypot_harness(planted, reg=reg, enforcing=True)
    c = rep.counts()
    # the enforce posture STILL blocks every attempted call, incident error or not
    assert c[ATTEMPTED_BLOCKED] > 0
    assert c[EXECUTED_ALLOWED] == 0
    for o in rep.outcomes:
        if o.called_honeypot:
            assert o.outcome == ATTEMPTED_BLOCKED and o.enforced is True


# -- (c) controls ------------------------------------------------------------ #

def test_normal_allowed_call_still_allows():
    _reg, gw, s = _enforcing_setup()
    assert gw.evaluate_tool_call(s.session_id, "http.get", {}).action == "allow"


def test_normal_policy_deny_still_denies():
    rules = [Rule(rule_id="r1", lane="lane1", action="deny",
                  matcher={"tool": "shell.exec"}, origin="tier_posture:C")]
    _reg, gw, s = _enforcing_setup(rules=rules)
    assert gw.evaluate_tool_call(s.session_id, "shell.exec", {}).action == "deny"


def test_unrelated_detection_error_is_not_misclassified_as_deny(monkeypatch):
    """A canary DETECTION failure (we could not even check) on an otherwise
    allowed call must fall through to ALLOW — a transient, unrelated error must
    NOT be upgraded into a forced deny of a legitimate call."""
    _reg, gw, s = _enforcing_setup()
    monkeypatch.setattr(CanaryManager, "check", _boom("registry read failed"))
    d = gw.evaluate_tool_call(s.session_id, "http.get", {})
    assert d.action == "allow"      # detection error ≠ deny


def test_stage_gate_detection_error_does_not_force_deny(monkeypatch):
    """If the (optional) stage gate cannot be COMPUTED, an allowed call still
    allows — the computation error is surfaced, not turned into a deny."""
    _reg, gw, s = _enforcing_setup()
    monkeypatch.setattr("agenttic.release.ladder.stage_gate",
                        _boom("ladder unavailable"))
    d = gw.evaluate_tool_call(s.session_id, "http.get", {},
                              caller_cohort="cohort-x")
    assert d.action == "allow"


def test_confirmed_deny_survives_a_decision_log_failure(monkeypatch):
    """A confirmed canary block whose *decision log* append raises must still
    return a DENY (never propagate/allow)."""
    reg, gw, s = _enforcing_setup()
    calls = {"n": 0}
    orig_append = reg.append_enforcement_event

    def flaky(event):
        # let the canary incident event through, fail the deny-decision append
        if event.kind == "decision":
            raise RuntimeError("append-only store down")
        return orig_append(event)

    monkeypatch.setattr(reg, "append_enforcement_event", flaky)
    d = gw.evaluate_tool_call(s.session_id, "delete_all_records", {"confirm": "y"})
    assert d.action == "deny"


# -- (d) the fail-closed deny is surfaced, not swallowed --------------------- #

def test_fail_closed_is_surfaced_to_telemetry_and_log(monkeypatch):
    reg, gw, s = _enforcing_setup()
    metrics.reset()
    monkeypatch.setattr("agenttic.live.incidents.open_manual", _boom())
    d = gw.evaluate_tool_call(s.session_id, "delete_all_records", {"confirm": "y"})
    assert d.action == "deny"
    # telemetry counter incremented for the canary fail-closed
    rendered = metrics.render()
    assert "agenttic_enforcement_fail_closed_total" in rendered
    assert 'origin="canary"' in rendered
    # an append-only admin event carries the surfaced enforcement error
    events = reg.list_enforcement_events(s.session_id)
    assert any(e.get("detail", {}).get("enforcement_error") == "canary_trip"
               for e in events)
