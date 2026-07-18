"""SPEC-4 Step 20 — the "moat" API: lineage, calibration, escalations.

Acceptance highlights:
- lineage returns the agent-config family tree with verbatim gate receipts
  (incl. a rejected sibling);
- a calibration label POSTed from the console lands in the same store the CLI
  `calibrate` reads;
- an escalation answered via the API produces the IDENTICAL registry state as
  the programmatic Step-12 HumanChannel path (20.3).
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from agenttic.learning.optimizer import AgentConfig
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.feedback import HumanFeedback
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import Span, Trace, SCHEMA_VERSION
from agenttic.server.app import create_app

CONFIG_YAML = """\
models: {agent_default: agent-model, judge_executor: judge-x, judge_strong: judge-model, judge_light: judge-light, generator: gen}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
security: {blackbox_block_private: false}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
judge_learning: {min_labels: 20}
auth: {token: "adm", required: true, allow_signup: true, signup_role: operator, session_secret: testsecret}
"""

AGENT = "triage-bot"
T0 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _cfg(agent_config_hash, parent, status, reason):
    return AgentConfig(agent_id=AGENT, agent_config_hash=agent_config_hash,
                       parent_hash=parent, diff_summary="d", status=status,
                       reason=reason, created_at=T0)


def _esc_trace(tid, question):
    span = Span(span_id=tid[:12], kind="escalation", name="human_escalation",
                start_time=T0, end_time=T0,
                input={"question": question, "context": {"tool": "issue_refund",
                       "autonomy_policy": "issue_refund: human_required"}})
    return Trace(trace_id=tid, agent_id=AGENT, agent_config_hash="h",
                 test_case_id="c1", spans=[span], visibility="glass_box",
                 final_output="ESCALATED_UNRESOLVED", escalated=True,
                 schema_version=SCHEMA_VERSION)


@pytest.fixture
def ctx(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                                  "c": tmp_path / "cal"})
    reg = Registry(tmp_path / "a.db")
    # lineage: baseline -> promoted, + a rejected sibling
    reg.save_agent_config(_cfg("h_base", "", "promoted", "baseline"))
    reg.save_agent_config(_cfg("h_promo", "h_base", "promoted",
                               "promoted: success 0.7->0.9; no regression > eps"))
    reg.save_agent_config(_cfg("h_rej", "h_base", "rejected",
                               "rejected: injection_robustness regressed 0.9->0.7"))
    # an escalation awaiting a human
    reg.save_trace(_esc_trace("esc1", "Authorize issue_refund($5000)?"))
    # a labelable trace: its case belongs to a registered suite (labels are
    # suite-keyed, so the endpoint resolves trace -> case -> suite).
    reg.save_rubric(Rubric(rubric_id="rb1", version=1, criteria=[
        Criterion(criterion_id="tone", description="courteous", scorer="judge",
                  scale="three_point", anchors={"pass": "warm", "fail": "curt"})]))
    reg.save_suite(TestSuite(suite_id="s1", version=1, approved=True,
                             business_context="pilot", test_ids=["c1"]),
                   [TestCase(test_id="c1", suite_id="s1", version=1,
                             task_description="t", rubric_id="rb1")])
    reg.save_trace(Trace(trace_id="tr_lab", agent_id=AGENT, agent_config_hash="h",
                         test_case_id="c1", spans=[], visibility="black_box",
                         final_output="a reply", schema_version=SCHEMA_VERSION))
    client = TestClient(create_app(str(cfg), registry=reg, clients={}))
    client._reg = reg  # type: ignore[attr-defined]
    with client as c:
        c._reg = reg  # type: ignore[attr-defined]
        yield c


def _adm():
    return {"Authorization": "Bearer adm"}


class TestLineage:
    def test_family_tree_with_gate_receipts_and_rejected_sibling(self, ctx):
        r = ctx.get(f"/api/lineage/agents/{AGENT}", headers=_adm())
        assert r.status_code == 200, r.text
        d = r.json()
        by_hash = {n["hash"]: n for n in d["nodes"]}
        assert set(by_hash) == {"h_base", "h_promo", "h_rej"}
        # the rejected sibling is present and carries its verbatim reason
        assert by_hash["h_rej"]["status"] == "rejected"
        assert "injection_robustness regressed" in by_hash["h_rej"]["gate_receipt"]["reason"]
        # the promoted child chains to the baseline
        assert by_hash["h_promo"]["parent_hash"] == "h_base"


class TestCalibration:
    def test_label_lands_in_the_calibrate_store(self, ctx):
        # a label POSTed from the console is readable via load_labels — the same
        # collection the CLI `calibrate` consumes.
        r = ctx.post("/api/calibration/labels", headers=_adm(),
                     json={"trace_id": "tr_lab", "criterion_id": "tone", "score": 1.0,
                           "suite_id": "s1"})
        assert r.status_code == 200, r.text
        from agenttic.scoring.calibration import load_labels
        # locate the CSV the endpoint wrote and confirm the pair is there —
        # load_labels is exactly what the CLI `calibrate` reads.
        found = False
        for root, _dirs, files in os.walk(os.path.dirname(
                ctx._reg.engine.url.database)):
            for f in files:
                if f.endswith(".csv"):
                    labels = load_labels(os.path.join(root, f))
                    if labels.get(("tr_lab", "tone")) == 1.0:
                        found = True
        assert found, "posted label not found in the calibration store"

    def test_off_scale_label_rejected(self, ctx):
        r = ctx.post("/api/calibration/labels", headers=_adm(),
                     json={"trace_id": "tr_lab", "criterion_id": "tone", "score": 0.7,
                           "suite_id": "s1"})
        assert r.status_code >= 400  # 0.7 is off the {0, 0.5, 1} scale


class TestEscalations:
    def test_pending_listed_with_question_and_policy(self, ctx):
        r = ctx.get("/api/escalations", headers=_adm())
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["pending_count"] >= 1
        p = next(p for p in d["pending"] if p["trace_id"] == "esc1")
        assert "issue_refund" in str(p)

    def test_respond_matches_programmatic_humanchannel_path(self, ctx):
        """20.3: a console resolution leaves the IDENTICAL registry state a
        programmatic HumanChannel resolution would — an append-only
        HumanFeedback(source='escalation', kind='escalation_decision')."""
        resp = "Approved $50 credit; DENIED the $5000 refund (out of policy)."
        r = ctx.post("/api/escalations/esc1/respond", headers=_adm(),
                     json={"response": resp})
        assert r.status_code == 200, r.text

        stored = ctx._reg.feedback_for_trace("esc1")
        assert len(stored) == 1
        fb = stored[0]
        # exactly what harness.runner._persist_escalation_feedback writes
        assert fb.source == "escalation"
        assert fb.kind == "escalation_decision"
        assert fb.rationale == resp
        assert fb.trace_id == "esc1"
        assert fb.agent_id == AGENT

        # and it drops out of the pending set
        d2 = ctx.get("/api/escalations", headers=_adm()).json()
        assert all(p["trace_id"] != "esc1" for p in d2["pending"])

    def test_respond_unknown_trace_404(self, ctx):
        r = ctx.post("/api/escalations/nope/respond", headers=_adm(),
                     json={"response": "x"})
        assert r.status_code == 404
