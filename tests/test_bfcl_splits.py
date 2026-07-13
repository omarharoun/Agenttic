"""BFCL v3 harder splits (parallel / multiple / parallel_multiple / live_*):
each split parses real vendored records into valid testcases, and the
multi-call ground truth scores through the canonical tool-call-accuracy checks
(selection / parameters / sequencing)."""

import uuid
from datetime import datetime, timezone

from agenttic.metrics.canonical_checks import (
    tool_selection_accuracy, tool_sequence_accuracy)
from agenttic.metrics.datasets import get_adapter
from agenttic.metrics.datasets.bfcl import (
    BFCLLiveMultipleAdapter, BFCLLiveSimpleAdapter, BFCLMultipleAdapter,
    BFCLParallelAdapter, BFCLParallelMultipleAdapter)
from agenttic.metrics.standard_suites import DATASET_SUITE_IDS, canonical_suite_ids
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.scoring.engine import score_run

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)

SPLIT_ADAPTERS = [
    ("bfcl-parallel", "bfcl-parallel-v3", BFCLParallelAdapter, 20),
    ("bfcl-multiple", "bfcl-multiple-v3", BFCLMultipleAdapter, 20),
    ("bfcl-parallel-multiple", "bfcl-parallel-multiple-v3",
     BFCLParallelMultipleAdapter, 20),
    ("bfcl-live-simple", "bfcl-live-simple-v3", BFCLLiveSimpleAdapter, 18),
    ("bfcl-live-multiple", "bfcl-live-multiple-v3", BFCLLiveMultipleAdapter, 15),
]


def _trace(tid, calls):
    """calls: list of (tool_name, args)."""
    spans = [Span(span_id=f"t{i}", kind="tool_call", name=tool,
                  start_time=NOW, end_time=NOW, input=args)
             for i, (tool, args) in enumerate(calls)]
    spans.append(Span(span_id="f", kind="final_output", name="final_output",
                      start_time=NOW, end_time=NOW))
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id=tid, visibility="glass_box", final_output="ok",
                 spans=spans, schema_version=SCHEMA_VERSION)


def test_each_split_parses_real_records_into_valid_testcases():
    for dataset_id, suite_id, Adapter, n in SPLIT_ADAPTERS:
        cases = Adapter().load_records()              # vendored real sample
        assert len(cases) == n, f"{suite_id}: {len(cases)} != {n}"
        for c in cases:
            assert c.suite_id == suite_id
            assert c.rubric_id == f"{suite_id}-rubric"
            assert c.expected["source"] == "BFCL"
            assert c.expected["split"] == dataset_id.removeprefix("bfcl-").replace("-", "_")
            # multi-call ground truth: an ordered list of expected calls, and a
            # deduped required-tool set derived from it.
            seq = c.expected["tool_sequence"]
            assert seq and len(seq) == len(c.expected["expected_calls"])
            assert c.expected["required_tools"] == list(dict.fromkeys(seq))
            assert c.input["question"] and c.input["functions"]


def test_get_adapter_resolves_new_splits():
    for dataset_id, suite_id, _Adapter, _n in SPLIT_ADAPTERS:
        assert get_adapter(dataset_id).info.suite_id == suite_id


def test_parallel_multi_call_ground_truth_requires_all_calls():
    # parallel_0: "Play ... Taylor Swift ... 20 minutes ... Maroon 5 ... 15"
    c0 = next(c for c in BFCLParallelAdapter().load_records()
              if c.expected["bfcl_id"] == "parallel_0")
    assert c0.expected["tool_sequence"] == ["spotify.play", "spotify.play"]
    assert c0.expected["required_tools"] == ["spotify.play"]

    both = _trace(c0.test_id, [("spotify.play", {"artist": "Taylor Swift", "duration": 20}),
                               ("spotify.play", {"artist": "Maroon 5", "duration": 15})])
    one = _trace(c0.test_id, [("spotify.play", {"artist": "Taylor Swift", "duration": 20})])
    # selection (set) is satisfied by either, but sequencing demands BOTH calls.
    assert tool_selection_accuracy(both, c0) == 1.0
    assert tool_sequence_accuracy(both, c0) == 1.0
    assert tool_sequence_accuracy(one, c0) == 0.0     # a single call is not enough


def test_parallel_multiple_case_scores_through_tool_call_accuracy(tmp_path):
    reg = Registry(tmp_path / "b.db")
    summary = BFCLParallelMultipleAdapter().ingest(reg)
    assert summary["ingested"] == 20 and summary["license"] == "Apache-2.0"
    _suite, cases = reg.get_suite("bfcl-parallel-multiple-v3")
    rubric = reg.get_rubric("bfcl-parallel-multiple-v3-rubric")
    # parallel_multiple_0: sum_of_multiples(1,1000,[3,5]) + product_of_primes(5)
    c0 = next(c for c in cases if c.expected["bfcl_id"] == "parallel_multiple_0")
    good = _trace(c0.test_id, [
        ("math_toolkit.sum_of_multiples",
         {"lower_limit": 1, "upper_limit": 1000, "multiples": [3, 5]}),
        ("math_toolkit.product_of_primes", {"count": 5})])
    rs = score_run(good, c0, rubric)
    assert rs.scoring_error is None and rs.passed is True
    # wrong selection (missing the second call's function) fails selection.
    bad = _trace(c0.test_id, [("math_toolkit.sum_of_multiples",
                               {"lower_limit": 1, "upper_limit": 1000, "multiples": [3, 5]})])
    assert tool_selection_accuracy(bad, c0) == 0.0


def test_multiple_selection_among_several_functions():
    # multiple split: several functions offered, pick the right one.
    cases = BFCLMultipleAdapter().load_records()
    c0 = next(c for c in cases if c.expected["bfcl_id"] == "multiple_0")
    assert len(c0.input["functions"]) > 1              # genuine select-among-many
    right = c0.expected["required_tools"][0]
    correct = _trace(c0.test_id, [(right, {})])
    wrong = _trace(c0.test_id, [("some_other_function", {})])
    assert tool_selection_accuracy(correct, c0) == 1.0
    assert tool_selection_accuracy(wrong, c0) == 0.0


def test_new_split_suites_registered_and_feed_index(tmp_path):
    reg = Registry(tmp_path / "b.db")
    for _did, suite_id, Adapter, _n in SPLIT_ADAPTERS:
        assert suite_id in DATASET_SUITE_IDS
        Adapter().ingest(reg)
        suite, _ = reg.get_suite(suite_id)
        assert suite.approved is True
        assert "SEED SAMPLE" in suite.business_context  # default ingest = vendored sample
        assert "REAL public dataset" not in suite.business_context
        assert "Apache-2.0" in suite.business_context
        assert suite_id in canonical_suite_ids(reg)    # rolls into the index


def test_idempotent_ingest(tmp_path):
    reg = Registry(tmp_path / "b.db")
    for _did, _suite_id, Adapter, _n in SPLIT_ADAPTERS:
        first = Adapter().ingest(reg)
        again = Adapter().ingest(reg)
        assert first["already_present"] is False
        assert again["already_present"] is True
