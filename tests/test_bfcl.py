"""BFCL ingestion: parses real vendored BFCL records into canonical testcases,
scores through tool-call accuracy, and labels the suite as the real dataset."""

import uuid
from datetime import datetime, timezone

from agenttic.metrics.canonical_checks import tool_param_accuracy
from agenttic.metrics.datasets.bfcl import BFCLAdapter
from agenttic.metrics.standard_suites import DATASET_SUITE_IDS, canonical_suite_ids
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.scoring.engine import score_run

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _trace(tid, tool, args):
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id=tid, visibility="glass_box", final_output="ok",
                 spans=[Span(span_id="t", kind="tool_call", name=tool,
                             start_time=NOW, end_time=NOW, input=args),
                        Span(span_id="f", kind="final_output", name="final_output",
                             start_time=NOW, end_time=NOW)],
                 schema_version=SCHEMA_VERSION)


def test_parses_real_records_with_ground_truth():
    cases = BFCLAdapter().load_records()           # vendored 25-record sample
    assert len(cases) == 25
    by_id = {c.expected["bfcl_id"]: c for c in cases}
    c0 = by_id["simple_0"]                          # the area-of-triangle case
    assert c0.expected["source"] == "BFCL"
    assert c0.expected["required_tools"] == ["calculate_triangle_area"]
    assert "base" in c0.expected["tool_args"]["calculate_triangle_area"]
    assert c0.rubric_id == "bfcl-simple-v3-rubric"


def test_list_valued_params_match_bfcl_ground_truth():
    # BFCL allows multiple acceptable values per arg; "" marks an arg optional
    tc = TestCase(test_id="x", suite_id="s", task_description="t", input={},
                  rubric_id="r", expected={"tool_args": {"f": {"a": ["10"], "u": ["units", ""]}}})
    assert tool_param_accuracy(_trace("x", "f", {"a": 10, "u": "units"}), tc) == 1.0
    assert tool_param_accuracy(_trace("x", "f", {"a": 10}), tc) == 1.0          # u optional
    assert tool_param_accuracy(_trace("x", "f", {"a": 99, "u": "x"}), tc) == 0.0


def test_bfcl_case_scores_through_tool_call_accuracy(tmp_path):
    reg = Registry(tmp_path / "b.db")
    summary = BFCLAdapter().ingest(reg)
    assert summary["ingested"] == 25 and summary["license"] == "Apache-2.0"
    suite, cases = reg.get_suite("bfcl-simple-v3")
    rubric = reg.get_rubric("bfcl-simple-v3-rubric")
    c0 = next(c for c in cases if c.expected["bfcl_id"] == "simple_0")
    good = _trace(c0.test_id, "calculate_triangle_area", {"base": 10, "height": 5, "unit": "units"})
    rs = score_run(good, c0, rubric)
    assert rs.scoring_error is None and rs.passed is True


def test_suite_labeled_real_dataset(tmp_path):
    reg = Registry(tmp_path / "b.db")
    BFCLAdapter().ingest(reg)
    suite, _ = reg.get_suite("bfcl-simple-v3")
    assert suite.approved is True
    assert "SEED SAMPLE" in suite.business_context  # default ingest = vendored sample
    assert "REAL public dataset" not in suite.business_context
    assert "Apache-2.0" in suite.business_context
    assert "bfcl-simple-v3" in DATASET_SUITE_IDS
    assert "bfcl-simple-v3" in canonical_suite_ids(reg)   # feeds the index


def test_idempotent_ingest(tmp_path):
    reg = Registry(tmp_path / "b.db")
    BFCLAdapter().ingest(reg)
    again = BFCLAdapter().ingest(reg)
    assert again["already_present"] is True
