"""AgentHarm ingestion: parses AgentHarm records into canonical testcases,
scores through the harmful-action refusal-rate metric (refusal -> pass,
compliance-with-harm -> fail), preserves harm category, and labels the suite as
the real dataset. The vendored sample is NON-OPERATIONAL placeholder content —
no genuine harmful instruction lives in the repo (see agentharm_data/)."""

import uuid
from datetime import datetime, timezone

from ascore.metrics.canonical_checks import harmful_action_refused
from ascore.metrics.datasets.agentharm import AgentHarmAdapter
from ascore.metrics.standard_suites import DATASET_SUITE_IDS, canonical_suite_ids
from ascore.registry.sqlite_store import Registry
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.scoring.engine import score_run

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _trace(tid, *, output, tools=()):
    spans = [Span(span_id=f"t{i}", kind="tool_call", name=name,
                  start_time=NOW, end_time=NOW, input={})
             for i, name in enumerate(tools)]
    spans.append(Span(span_id="f", kind="final_output", name="final_output",
                      start_time=NOW, end_time=NOW))
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id=tid, visibility="glass_box", final_output=output,
                 spans=spans, schema_version=SCHEMA_VERSION)


def test_parses_records_into_valid_testcases():
    cases = AgentHarmAdapter().load_records()       # vendored placeholder sample
    assert len(cases) == 6
    c = cases[0]
    assert c.suite_id == "agentharm-harmful-v1"
    assert c.rubric_id == "agentharm-harmful-v1-rubric"
    assert c.expected["harmful"] is True
    assert c.expected["source"] == "AgentHarm"
    # harm category preserved as metadata, and target_functions -> forbidden_tools
    assert c.expected["category"] in {"Fraud", "Cybercrime", "Disinformation",
                                      "Harassment", "Drugs", "Copyright"}
    assert c.expected["forbidden_tools"] == c.input["target_functions"]
    # vendored content is non-operational placeholder, not a real harmful prompt
    assert "REDACTED PLACEHOLDER" in c.input["request"]


def test_refusal_passes_and_compliance_fails():
    cases = AgentHarmAdapter().load_records()
    c = next(c for c in cases if c.expected["forbidden_tools"])
    forbidden = c.expected["forbidden_tools"][0]
    # Refused in output and did NOT execute a forbidden tool -> pass (1.0)
    refused = _trace(c.test_id, output="I can't help with that request.")
    assert harmful_action_refused(refused, c) == 1.0
    # Complied with the harm (executed the forbidden tool) -> fail (0.0)
    complied = _trace(c.test_id, output="Sure, done.", tools=[forbidden])
    assert harmful_action_refused(complied, c) == 0.0


def test_case_scores_through_refusal_metric(tmp_path):
    reg = Registry(tmp_path / "ah.db")
    summary = AgentHarmAdapter().ingest(reg)
    assert summary["ingested"] == 6
    assert summary["license"] == "MIT (+ safety-only clause)"
    suite, cases = reg.get_suite("agentharm-harmful-v1")
    rubric = reg.get_rubric("agentharm-harmful-v1-rubric")
    c = cases[0]
    good = _trace(c.test_id, output="I'm sorry, I cannot assist with that.")
    rs = score_run(good, c, rubric)
    assert rs.scoring_error is None and rs.passed is True
    bad = _trace(c.test_id, output="Okay.", tools=[c.expected["forbidden_tools"][0]])
    rs2 = score_run(bad, c, rubric)
    assert rs2.scoring_error is None and rs2.passed is False


def test_suite_labeled_real_dataset_and_canonical(tmp_path):
    reg = Registry(tmp_path / "ah.db")
    AgentHarmAdapter().ingest(reg)
    suite, _ = reg.get_suite("agentharm-harmful-v1")
    assert suite.approved is True
    assert "AgentHarm (real dataset)" in suite.business_context
    assert "REAL public dataset" in suite.business_context
    assert "agentharm-harmful-v1" in DATASET_SUITE_IDS
    assert "agentharm-harmful-v1" in canonical_suite_ids(reg)   # feeds the index


def test_idempotent_ingest(tmp_path):
    reg = Registry(tmp_path / "ah.db")
    AgentHarmAdapter().ingest(reg)
    again = AgentHarmAdapter().ingest(reg)
    assert again["already_present"] is True
