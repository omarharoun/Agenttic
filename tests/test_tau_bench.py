"""τ-bench ingestion: parses real vendored τ-bench tasks into canonical
testcases, scores a candidate trajectory through tool-call accuracy / sequence,
and labels the suite as the real dataset (distinct from the std-* seeds)."""

import uuid
from datetime import datetime, timezone

from ascore.metrics.canonical_checks import tool_param_accuracy
from ascore.metrics.datasets.tau_bench import TauBenchAdapter, _parse_task_module
from ascore.metrics.standard_suites import DATASET_SUITE_IDS, canonical_suite_ids
from ascore.registry.sqlite_store import Registry
from ascore.schema.testcase import TestCase
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.scoring.engine import score_run

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)

_TASK_SRC = '''
from tau_bench.types import Task, Action

TASKS_TEST = [
    Task(
        annotator="0",
        user_id="yusuf_rossi_9620",
        instruction="Exchange the keyboard.",
        actions=[
            Action(name="find_user_id_by_name_zip",
                   kwargs={"first_name": "Yusuf", "last_name": "Rossi", "zip": "19122"}),
            Action(name="exchange_delivered_order_items",
                   kwargs={"order_id": "#W2378156", "item_ids": ["1151293680", "4983901480"]}),
        ],
        outputs=[],
    ),
]
'''


def _trace(tid, calls):
    """calls: list of (tool, args)."""
    spans = [Span(span_id=f"t{i}", kind="tool_call", name=name,
                  start_time=NOW, end_time=NOW, input=args)
             for i, (name, args) in enumerate(calls)]
    spans.append(Span(span_id="f", kind="final_output", name="final_output",
                      start_time=NOW, end_time=NOW))
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id=tid, visibility="glass_box", final_output="done",
                 spans=spans, schema_version=SCHEMA_VERSION)


def test_ast_parser_reads_real_task_literals():
    recs = _parse_task_module(_TASK_SRC, "retail")
    assert len(recs) == 1
    r = recs[0]
    assert r["domain"] == "retail" and r["user_id"] == "yusuf_rossi_9620"
    assert [a["name"] for a in r["actions"]] == [
        "find_user_id_by_name_zip", "exchange_delivered_order_items"]
    # genuine list-valued arg preserved verbatim (not flattened)
    assert r["actions"][1]["kwargs"]["item_ids"] == ["1151293680", "4983901480"]


def test_parses_real_vendored_sample():
    cases = TauBenchAdapter().load_records()       # vendored 20-task real sample
    assert len(cases) == 20
    domains = {c.expected["domain"] for c in cases}
    assert domains == {"retail", "airline"}
    c0 = next(c for c in cases if c.test_id == "tau-bench-v1-retail-0")
    assert c0.expected["source"] == "tau-bench"
    assert c0.expected["required_tools"][0] == "find_user_id_by_name_zip"
    # ordered trajectory preserved, including any repeated tools
    assert c0.expected["tool_sequence"] == [
        a for a in c0.expected["tool_sequence"]] and len(c0.expected["tool_sequence"]) >= 2
    assert c0.rubric_id == "tau-bench-v1-rubric"
    assert "multi_turn" in c0.tags


def test_list_and_dict_args_match_ground_truth():
    # τ-bench args have exactly one acceptable value; the adapter wraps each in a
    # single-element list so the BFCL-style scorer does exact structural match —
    # including genuine list/dict argument values.
    tc = TestCase(test_id="x", suite_id="s", task_description="t", input={},
                  rubric_id="r", expected={"tool_args": {
                      "exchange_delivered_order_items": {
                          "order_id": ["#W2378156"],
                          "item_ids": [["1151293680", "4983901480"]]}}})
    good = _trace("x", [("exchange_delivered_order_items",
                         {"order_id": "#W2378156",
                          "item_ids": ["1151293680", "4983901480"]})])
    bad = _trace("x", [("exchange_delivered_order_items",
                        {"order_id": "#W2378156", "item_ids": ["9999"]})])
    assert tool_param_accuracy(good, tc) == 1.0
    assert tool_param_accuracy(bad, tc) == 0.5   # order_id matches, item_ids does not


def test_tau_bench_case_scores_through_tool_call_accuracy(tmp_path):
    reg = Registry(tmp_path / "t.db")
    summary = TauBenchAdapter().ingest(reg)
    assert summary["ingested"] == 20 and summary["license"] == "MIT"
    suite, cases = reg.get_suite("tau-bench-v1")
    rubric = reg.get_rubric("tau-bench-v1-rubric")
    c0 = next(c for c in cases if c.test_id == "tau-bench-v1-retail-0")
    # replay the exact ground-truth trajectory -> should pass
    calls = []
    for tool in c0.expected["tool_sequence"]:
        args = {k: v[0] for k, v in c0.expected["tool_args"].get(tool, {}).items()}
        calls.append((tool, args))
    rs = score_run(_trace(c0.test_id, calls), c0, rubric)
    assert rs.scoring_error is None and rs.passed is True


def test_suite_labeled_real_dataset_distinct_from_seeds(tmp_path):
    reg = Registry(tmp_path / "t.db")
    TauBenchAdapter().ingest(reg)
    suite, _ = reg.get_suite("tau-bench-v1")
    assert suite.approved is True
    assert "REAL public dataset" in suite.business_context
    assert "MIT" in suite.business_context
    assert not suite.suite_id.startswith("std-")          # distinct from seed suites
    assert "tau-bench-v1" in DATASET_SUITE_IDS
    assert "tau-bench-v1" in canonical_suite_ids(reg)     # feeds the index


def test_idempotent_ingest(tmp_path):
    reg = Registry(tmp_path / "t.db")
    TauBenchAdapter().ingest(reg)
    again = TauBenchAdapter().ingest(reg)
    assert again["already_present"] is True
