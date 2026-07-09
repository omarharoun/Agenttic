"""InjecAgent ingestion: parses real InjecAgent records (indirect prompt
injection for tool-using agents) into canonical testcases scored by the
prompt-injection robustness metric (resisted -> robust/pass, followed -> fail),
preserves the attack category (direct-harm vs data-stealing), and labels the
suite as the real dataset. A small REAL sample is vendored (MIT) under
injecagent_data/."""

import uuid
from datetime import datetime, timezone

from ascore.metrics.canonical_checks import injection_robust
from ascore.metrics.datasets.injecagent import InjecAgentAdapter
from ascore.metrics.standard_suites import DATASET_SUITE_IDS, canonical_suite_ids
from ascore.registry.sqlite_store import Registry
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.scoring.engine import score_run

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _trace(tid, *, output="Here is the laptop summary you asked for.", tools=()):
    spans = [Span(span_id=f"t{i}", kind="tool_call", name=name,
                  start_time=NOW, end_time=NOW, input={})
             for i, name in enumerate(tools)]
    spans.append(Span(span_id="f", kind="final_output", name="final_output",
                      start_time=NOW, end_time=NOW))
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id=tid, visibility="glass_box", final_output=output,
                 spans=spans, schema_version=SCHEMA_VERSION)


def test_parses_real_records_into_valid_testcases():
    cases = InjecAgentAdapter().load_records()       # vendored real sample
    assert len(cases) == 16                           # 8 direct-harm + 8 data-stealing
    for c in cases:
        assert c.suite_id == "injecagent-v1"
        assert c.rubric_id == "injecagent-v1-rubric"
        assert c.expected["source"] == "InjecAgent"
        # the injection target is a concrete attacker tool the check can score
        assert c.expected["injection_target"]
        assert c.expected["injection_target"] == c.input["attacker_tools"][0]
        # the user-tool output actually carries the spliced-in attacker instruction
        assert c.input["attacker_instruction"][:30] in c.input["content"]
    # both attack categories preserved as metadata
    assert {c.expected["attack_category"] for c in cases} == {"direct-harm",
                                                              "data-stealing"}


def test_resisted_injection_passes_and_followed_fails():
    cases = InjecAgentAdapter().load_records()
    c = cases[0]
    target = c.expected["injection_target"]
    # Served the user and did NOT call the injected attacker tool -> robust (1.0)
    robust = _trace(c.test_id, output="Here are the Dell laptop details.")
    assert injection_robust(robust, c) == 1.0
    # Hijacked into executing the injected attacker tool -> ASR hit (0.0)
    hijacked = _trace(c.test_id, output="Done.", tools=[target])
    assert injection_robust(hijacked, c) == 0.0


def test_case_scores_through_injection_robustness_metric(tmp_path):
    reg = Registry(tmp_path / "ia.db")
    summary = InjecAgentAdapter().ingest(reg)
    assert summary["ingested"] == 16
    assert summary["license"] == "MIT"
    assert summary["dataset"] == "injecagent"
    suite, cases = reg.get_suite("injecagent-v1")
    rubric = reg.get_rubric("injecagent-v1-rubric")
    c = cases[0]
    good = _trace(c.test_id, output="Here is the product summary.")
    rs = score_run(good, c, rubric)
    assert rs.scoring_error is None and rs.passed is True
    bad = _trace(c.test_id, output="Okay.", tools=[c.expected["injection_target"]])
    rs2 = score_run(bad, c, rubric)
    assert rs2.scoring_error is None and rs2.passed is False


def test_suite_labeled_real_dataset_and_canonical(tmp_path):
    reg = Registry(tmp_path / "ia.db")
    InjecAgentAdapter().ingest(reg)
    suite, _ = reg.get_suite("injecagent-v1")
    assert suite.approved is True
    assert "InjecAgent (real dataset)" in suite.business_context
    assert "SEED SAMPLE" in suite.business_context  # default ingest = vendored sample
    assert "REAL public dataset" not in suite.business_context
    # distinct from the seed safety-injection suite
    assert suite.suite_id == "injecagent-v1" != "std-safety-injection-v1"
    assert "injecagent-v1" in DATASET_SUITE_IDS
    assert "injecagent-v1" in canonical_suite_ids(reg)   # feeds the index rollup


def test_idempotent_ingest(tmp_path):
    reg = Registry(tmp_path / "ia.db")
    first = InjecAgentAdapter().ingest(reg)
    assert first["already_present"] is False
    again = InjecAgentAdapter().ingest(reg)
    assert again["already_present"] is True
    assert again["ingested"] == 0
