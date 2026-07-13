"""GAIA ingestion: parses GAIA validation records (question + final answer +
level) into canonical testcases, scores through the GAIA-normalized exact-match
answer-accuracy metric, surfaces the dataset's GATED status, and labels the
suite as the real dataset. GAIA is access-gated, so the vendored sample is
CLEARLY-FABRICATED, NON-OPERATIONAL placeholder content mirroring only the record
schema — no real GAIA question/answer lives in the repo (see gaia_data/)."""

import uuid
from datetime import datetime, timezone

from agenttic.metrics.canonical_checks import gaia_answer_match, gaia_question_scorer
from agenttic.metrics.datasets import dataset_infos, get_adapter
from agenttic.metrics.datasets.gaia import GAIAAdapter
from agenttic.metrics.standard_suites import DATASET_SUITE_IDS, canonical_suite_ids
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.scoring.engine import score_run

NOW = datetime(2026, 6, 22, tzinfo=timezone.utc)


def _trace(tid, *, output):
    span = Span(span_id="f", kind="final_output", name="final_output",
                start_time=NOW, end_time=NOW)
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id=tid, visibility="glass_box", final_output=output,
                 spans=[span], schema_version=SCHEMA_VERSION)


# -- parsing ---------------------------------------------------------------

def test_parses_records_into_valid_testcases():
    cases = GAIAAdapter().load_records()        # vendored placeholder sample
    assert len(cases) == 5
    c = cases[0]
    assert c.suite_id == "gaia-v1"
    assert c.rubric_id == "gaia-v1-rubric"
    assert c.expected["source"] == "GAIA"
    assert c.expected["final_answer"]            # ground-truth answer preserved
    assert c.expected["gated"] is True
    assert c.input["question"]                   # question preserved
    assert c.expected["level"] in {1, 2, 3}
    # level surfaced as a tag for slicing
    assert any(t.startswith("level-") for t in c.tags)
    # vendored content is a clearly-labeled fabricated placeholder, not real GAIA
    assert "PLACEHOLDER" in c.input["question"]


def test_sample_comment_header_skipped():
    # the JSONL's leading _comment object must not become a test case
    cases = GAIAAdapter().load_records()
    assert all(c.input.get("gaia_task_id") for c in cases)


# -- GAIA normalized exact-match scorer -------------------------------------

def test_gaia_question_scorer_numbers_lists_strings():
    # numbers: separators / units stripped, compared as floats
    assert gaia_question_scorer("1,234", "1234") is True
    assert gaia_question_scorer("$10", "10") is True
    assert gaia_question_scorer("10%", "10") is True
    assert gaia_question_scorer("11", "10") is False
    # strings: case/punctuation/whitespace-insensitive
    assert gaia_question_scorer("Paris.", "paris") is True
    assert gaia_question_scorer("  GREEN ", "green") is True
    assert gaia_question_scorer("London", "Paris") is False
    # lists: element-wise, order matters, length must match
    assert gaia_question_scorer("2, 4, 6", "2,4,6") is True
    assert gaia_question_scorer("2; 4; 6", "2, 4, 6") is True
    assert gaia_question_scorer("2, 4", "2, 4, 6") is False


def test_gaia_answer_match_strips_final_answer_prefix():
    cases = GAIAAdapter().load_records()
    c = next(c for c in cases if c.expected["final_answer"] == "Paris")
    # agent emits the canonical "FINAL ANSWER:" prefix — still a match
    assert gaia_answer_match(_trace(c.test_id, output="FINAL ANSWER: Paris"), c) == 1.0
    assert gaia_answer_match(_trace(c.test_id, output="Paris"), c) == 1.0
    assert gaia_answer_match(_trace(c.test_id, output="London"), c) == 0.0


# -- end-to-end scoring -----------------------------------------------------

def test_case_scores_through_answer_accuracy_metric(tmp_path):
    reg = Registry(tmp_path / "gaia.db")
    summary = GAIAAdapter().ingest(reg)
    assert summary["ingested"] == 5
    suite, cases = reg.get_suite("gaia-v1")
    rubric = reg.get_rubric("gaia-v1-rubric")
    c = cases[0]
    good = _trace(c.test_id, output=c.expected["final_answer"])
    rs = score_run(good, c, rubric)
    assert rs.scoring_error is None and rs.passed is True
    bad = _trace(c.test_id, output="definitely not the answer")
    rs2 = score_run(bad, c, rubric)
    assert rs2.scoring_error is None and rs2.passed is False


def test_suite_labeled_real_dataset_and_gated_and_canonical(tmp_path):
    reg = Registry(tmp_path / "gaia.db")
    GAIAAdapter().ingest(reg)
    suite, _ = reg.get_suite("gaia-v1")
    assert suite.approved is True
    assert "GAIA validation (real dataset, gated)" in suite.business_context
    assert "SEED SAMPLE" in suite.business_context  # default ingest = vendored sample
    assert "REAL public dataset" not in suite.business_context
    assert "gaia-v1" in DATASET_SUITE_IDS
    assert "gaia-v1" in canonical_suite_ids(reg)        # feeds the index


def test_gated_status_surfaced():
    # adapter info marks GAIA gated so the UI/methodology shows "bring your own access"
    assert GAIAAdapter.info.gated is True
    infos = {i.dataset_id: i for i in dataset_infos()}
    assert "gaia" in infos
    assert infos["gaia"].gated is True
    # registered and resolvable by dataset_id
    assert isinstance(get_adapter("gaia"), GAIAAdapter)


def test_idempotent_ingest(tmp_path):
    reg = Registry(tmp_path / "gaia.db")
    first = GAIAAdapter().ingest(reg)
    assert first["already_present"] is False
    again = GAIAAdapter().ingest(reg)
    assert again["already_present"] is True
