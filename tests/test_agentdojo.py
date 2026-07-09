"""AgentDojo ingestion: parses real AgentDojo injection tasks into canonical
testcases scored by our prompt-injection robustness metric (resisted -> robust
-> pass; hijacked into the attacker's tool action -> fail), preserves the
suite/environment and attack type as metadata, labels the suite as the real
dataset, and ingests idempotently. The vendored sample is REAL data parsed
verbatim from the upstream MIT-licensed repo (see agentdojo_data/)."""

import uuid
from datetime import datetime, timezone

from ascore.metrics.canonical_checks import injection_robust
from ascore.metrics.datasets.agentdojo import AgentDojoAdapter
from ascore.metrics.standard_suites import DATASET_SUITE_IDS, canonical_suite_ids
from ascore.registry.sqlite_store import Registry
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.scoring.engine import score_run

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _trace(tid, *, output="Done.", tools=()):
    spans = [Span(span_id=f"t{i}", kind="tool_call", name=name,
                  start_time=NOW, end_time=NOW, input={})
             for i, name in enumerate(tools)]
    spans.append(Span(span_id="f", kind="final_output", name="final_output",
                      start_time=NOW, end_time=NOW))
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id=tid, visibility="glass_box", final_output=output,
                 spans=spans, schema_version=SCHEMA_VERSION)


def test_parses_records_into_valid_testcases():
    cases = AgentDojoAdapter().load_records()       # vendored real sample
    assert len(cases) == 8                          # 2 injection tasks × 4 suites
    suites = {c.expected["suite"] for c in cases}
    assert suites == {"banking", "slack", "travel", "workspace"}
    c = next(c for c in cases if c.expected["suite"] == "banking")
    assert c.suite_id == "agentdojo-v1"
    assert c.rubric_id == "agentdojo-v1-rubric"
    assert c.expected["source"] == "AgentDojo"
    assert c.expected["attack_type"] == "tool_action_hijack"
    # real attacker target tool parsed from ground_truth(), suite preserved
    assert c.expected["injection_target"] == "send_money"
    assert "injection" in c.tags and "banking" in c.tags
    # attacker goal is real text, not fabricated
    assert "US133000000121212121212" in c.expected["attacker_goal"]
    # utility-under-attack reference preserved as metadata (secondary signal)
    util = c.expected["utility_under_attack"]
    assert util and util["user_task"].startswith("UserTask")
    assert isinstance(util["legit_tools"], list) and util["legit_tools"]


def test_resisted_is_robust_hijacked_fails():
    c = next(c for c in AgentDojoAdapter().load_records()
             if c.expected["suite"] == "banking")
    target = c.expected["injection_target"]
    # Resisted the injection (never called the attacker's tool) -> robust (1.0)
    resisted = _trace(c.test_id, output="I won't do that.", tools=["get_balance"])
    assert injection_robust(resisted, c) == 1.0
    # Hijacked into the attacker's tool action -> not robust (0.0)
    hijacked = _trace(c.test_id, output="ok", tools=["get_balance", target])
    assert injection_robust(hijacked, c) == 0.0


def test_terminal_payload_tool_is_target_for_multistep_attack():
    # slack InjectionTask2's ground truth ends in the exfiltration tool
    # post_webpage; the terminal side-effecting call is the injection target.
    cases = AgentDojoAdapter().load_records()
    multi = next(c for c in cases if len(c.expected["injection_targets"]) > 1)
    assert multi.expected["injection_target"] == multi.expected["injection_targets"][-1]
    assert multi.expected["suite"] == "slack"


def test_case_scores_through_injection_metric(tmp_path):
    reg = Registry(tmp_path / "ad.db")
    summary = AgentDojoAdapter().ingest(reg)
    assert summary["ingested"] == 8
    assert summary["license"] == "MIT"
    assert summary["dataset"] == "agentdojo"
    suite, cases = reg.get_suite("agentdojo-v1")
    rubric = reg.get_rubric("agentdojo-v1-rubric")
    c = cases[0]
    target = c.expected["injection_target"]
    good = _trace(c.test_id, output="Refused.", tools=["get_balance"])
    rs = score_run(good, c, rubric)
    assert rs.scoring_error is None and rs.passed is True
    bad = _trace(c.test_id, output="ok", tools=[target])
    rs2 = score_run(bad, c, rubric)
    assert rs2.scoring_error is None and rs2.passed is False


def test_suite_labeled_real_dataset_and_canonical(tmp_path):
    reg = Registry(tmp_path / "ad.db")
    AgentDojoAdapter().ingest(reg)
    suite, _ = reg.get_suite("agentdojo-v1")
    assert suite.approved is True
    assert "AgentDojo (real dataset)" in suite.business_context
    assert "SEED SAMPLE" in suite.business_context  # default ingest = vendored sample
    assert "REAL public dataset" not in suite.business_context
    assert "agentdojo-v1" in DATASET_SUITE_IDS
    assert "agentdojo-v1" in canonical_suite_ids(reg)   # feeds the index


def test_idempotent_ingest(tmp_path):
    reg = Registry(tmp_path / "ad.db")
    first = AgentDojoAdapter().ingest(reg)
    assert first["already_present"] is False
    again = AgentDojoAdapter().ingest(reg)
    assert again["already_present"] is True
    assert again["ingested"] == 0
