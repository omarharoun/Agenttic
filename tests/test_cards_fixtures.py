"""T20.5 — card autofill + autonomy + agency fixtures (SPEC-2 M9)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

import pytest

from ascore.cards.agency import detect_covered_agent
from ascore.cards.autofill import autofill_card
from ascore.cards.autonomy import classify_autonomy
from ascore.certification.dossier import assemble
from ascore.config import load_config
from ascore.registry.sqlite_store import Registry
from ascore.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)
from ascore.schema.scorecard import CriterionScore, RunScore, Scorecard
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _tc(name, attrs=None):
    return Span(span_id=f"{name}-{id(attrs)}", kind="tool_call", name=name,
                start_time=NOW, end_time=NOW, attributes=attrs or {})


def _trace(tid, agent, spans, mode_final=True):
    s = list(spans)
    if mode_final:
        s.append(Span(span_id=f"f-{tid}", kind="final_output", name="final_output",
                      start_time=NOW, end_time=NOW, attributes={}))
    return Trace(trace_id=tid, agent_id=agent, agent_config_hash="h",
                 test_case_id=tid, visibility="glass_box", final_output="x",
                 spans=s, schema_version=SCHEMA_VERSION)


@pytest.fixture()
def cfg():
    return load_config("config.yaml")


def _rich_agent(reg, agent="ref-agent"):
    # batch traces with autonomous write actions (covered agent)
    reg.save_trace(_trace("t1", agent, [_tc("fs.write"), _tc("http.get")]), mode="batch")
    reg.save_trace(_trace("t2", agent, [_tc("http.post"), _tc("search.query")]), mode="batch")
    reg.save_trace(_trace("t3", agent, [_tc("shell.exec")]), mode="batch")
    # a live trace (monitoring)
    reg.save_trace(Trace(trace_id="live1", agent_id=agent, agent_config_hash="h",
                         test_case_id=None, visibility="glass_box", final_output="x",
                         spans=[Span(span_id="lf", kind="final_output",
                                     name="final_output", start_time=NOW,
                                     end_time=NOW, attributes={})],
                         schema_version=SCHEMA_VERSION), mode="live")
    # a scorecard (benchmarks)
    reg.save_scorecard(Scorecard(
        scorecard_id="sc1", agent_id=agent, suite_id="std-tool-use-v1",
        suite_version=1, rubric_id="r", rubric_version=1,
        run_scores=[RunScore(trace_id="t1", test_id="t1", passed=True,
                             criterion_scores=[CriterionScore(
                                 criterion_id="tool_selection_accuracy",
                                 score=1.0, scorer="code")])],
        task_success_rate=1.0, mean_cost_usd=0.0, p95_latency_ms=0.0,
        visibility_tier="glass_box"))
    # an incident
    from ascore.live.incidents import open_manual
    open_manual(reg, agent_id=agent, severity="S3", title="drift")
    # a dossier (certification)
    assemble(reg, agent_id=agent, agent_config_hash="h",
             profile=CertificationProfile(profile_id="p", required_domains=["tool_use"]),
             tier_decision=TierDecision(tier="B", evidence_refs=["e"]),
             coverage=[], attestation=Attestation(mode="self_attested",
                                                  tenant="default"))


def test_autofill_six_fields_with_resolvable_refs(cfg):
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        _rich_agent(reg)
        card = autofill_card(cfg, reg, "ref-agent")
        present = card.present_fields()
        assert len(present) >= 6, list(present)
        # every measured field's evidence refs resolve to a persisted id
        for fv in present.values():
            if fv.provenance != "measured":
                continue
            for ref in fv.evidence_refs:
                kind, _, ident = ref.partition(":")
                if kind == "trace":
                    reg.get_trace(ident)              # raises if missing
                elif kind == "scorecard":
                    reg.get_scorecard(ident)
                elif kind == "dossier":
                    reg.get_dossier(ident)
                elif kind == "incident":
                    assert any(i["incident_id"] == ident
                               for i in reg.list_incidents("ref-agent"))


def test_autonomy_approval_gated_le_l3(cfg):
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        reg.save_trace(_trace("t1", "a", [_tc("fs.write", {"requires_approval": True})]))
        c = classify_autonomy(reg, "a", cfg)
        assert c.level in ("L1", "L2", "L3")


def test_autonomy_unattended_ge_l4(cfg):
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        reg.save_trace(_trace("t1", "a", [_tc("fs.write"), _tc("http.post"), _tc("shell.exec")]))
        c = classify_autonomy(reg, "a", cfg)
        assert c.level in ("L4", "L5")


def test_autonomy_empty_is_none(cfg):
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        assert classify_autonomy(reg, "a", cfg).level is None


def test_covered_true_none_false(cfg):
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        _rich_agent(reg, "covered")
        assert detect_covered_agent(reg, "covered", cfg).covered is True
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        assert detect_covered_agent(reg, "sparse", cfg).covered is None  # no traces
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        for i in range(3):
            reg.save_trace(_trace(f"t{i}", "readonly", [_tc("http.get")]))
        assert detect_covered_agent(reg, "readonly", cfg).covered is False
