"""T16.3 — live trace ingest: live data never mixes into batch scorecards, and
drift escalates to an S3 incident on the live path."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone

from agenttic.live.incidents import escalate_drift, IncidentManager
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _trace(tid, agent, test_case_id):
    return Trace(trace_id=tid, agent_id=agent, agent_config_hash="h",
                 test_case_id=test_case_id, visibility="glass_box",
                 final_output="x",
                 spans=[Span(span_id="f", kind="final_output",
                             name="final_output", start_time=NOW, end_time=NOW,
                             attributes={})],
                 schema_version=SCHEMA_VERSION)


def test_live_traces_never_mix_into_batch():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        # a batch trace and a live trace for the same agent
        reg.save_trace(_trace("batch-1", "a1", "case-1"), mode="batch")
        reg.save_trace(_trace("live-1", "a1", None), mode="live")
        batch = list(reg.traces("a1", mode="batch"))
        live = list(reg.traces("a1", mode="live"))
        assert [t.trace_id for t in batch] == ["batch-1"]
        assert [t.trace_id for t in live] == ["live-1"]
        # the live trace never appears in the batch view
        assert "live-1" not in {t.trace_id for t in batch}


def test_drift_status_escalates_to_s3_incident():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")

        class _Status:
            agent_id = "a1"
            drift_detected = True
            drifted = ["harmful_refusal_judge"]

        inc = escalate_drift(reg, {"incidents": {"drift_default_severity": "S3"}},
                             _Status())
        assert inc is not None and inc.severity == "S3"
        assert IncidentManager(reg).current_state(inc.incident_id) == "open"
        assert reg.list_incidents("a1")[0]["origin"] == "drift"


def test_no_drift_opens_no_incident():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")

        class _Status:
            agent_id = "a1"
            drift_detected = False
            drifted = []

        assert escalate_drift(reg, {}, _Status()) is None
        assert reg.list_incidents("a1") == []
