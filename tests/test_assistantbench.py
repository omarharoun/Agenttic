"""AssistantBench ingestion + fractional answer-accuracy / answer-rate metrics.

Verifies the AssistantBench partial-credit scoring (exact=1.0, close numeric
within tolerance, wrong=0), that answer-rate counts abstentions, that the
adapter parses real vendored records, that the suite is labeled a real dataset
and feeds the index, and that ingest is idempotent.
"""

import uuid
from datetime import datetime, timezone

import pytest

from ascore.metrics import answer_match as am
from ascore.metrics import canonical_checks as cc
from ascore.metrics.catalog import BY_ID, CHECK_TO_METRIC, index_weights
from ascore.metrics.datasets.assistantbench import AssistantBenchAdapter
from ascore.metrics.index import compute_index, rollup_metrics_from_means
from ascore.metrics.standard_suites import DATASET_SUITE_IDS, canonical_suite_ids
from ascore.registry.sqlite_store import Registry
from ascore.schema.testcase import TestCase
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.scoring.checks import CHECKS
from ascore.scoring.engine import score_run

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _trace(final):
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id="tc", visibility="glass_box", final_output=final,
                 spans=[Span(span_id="f", kind="final_output", name="final_output",
                             start_time=NOW, end_time=NOW)],
                 schema_version=SCHEMA_VERSION)


def _tc(expected):
    return TestCase(test_id="tc", suite_id="s", task_description="t", input={},
                    expected=expected, rubric_id="r")


# -- fractional scoring (the AssistantBench metric) ------------------------

class TestFractionalAccuracy:
    def test_exact_string_is_full_credit(self):
        assert am.score_answer("Shanghai villa", "Shanghai villa") == 1.0
        # normalisation: case / articles / punctuation ignored -> still exact
        assert am.score_answer("the Shanghai Villa.", "Shanghai villa") == 1.0

    def test_partial_string_is_partial_credit(self):
        # one of two tokens overlaps -> between 0 and 1 (token-F1)
        s = am.score_answer("Shanghai cafe", "Shanghai villa")
        assert 0.0 < s < 1.0

    def test_wrong_string_is_zero(self):
        assert am.score_answer("completely unrelated text", "Shanghai villa") == 0.0

    def test_exact_number_is_full_credit(self):
        assert am.score_answer("1010000", "1010000") == 1.0
        assert am.score_answer("14.2", "14.2") == 1.0

    def test_close_number_within_tolerance_gets_partial_credit(self):
        # a number off by a small ratio scores high but < 1.0 (log-ratio decay)
        close = am.score_answer("15.0", "14.2")
        assert 0.5 < close < 1.0
        # off by more than a factor of e -> floored at 0
        assert am.score_answer("100", "14.2") == 0.0

    def test_wrong_type_number_is_zero(self):
        assert am.score_answer("not a number", "14.2") == 0.0

    def test_string_list_partial_credit(self):
        gold = "Trout lake trail\nArtist Point\nFountain Paint Pot"
        # two of three list items correct -> ~2/3, strictly between 0 and 1
        pred = "Trout lake trail\nArtist Point\nNonexistent Trail"
        s = am.score_answer(pred, gold)
        assert 0.5 < s < 1.0
        assert am.score_answer(gold, gold) == 1.0

    def test_json_dict_scoring(self):
        gold = '{"sender": "USPS", "price (usd)": "41.75"}'
        assert am.score_answer(gold, gold) == 1.0
        # right sender, wrong price -> partial credit, not zero, not full
        partial = am.score_answer('{"sender": "USPS", "price (usd)": "99.0"}', gold)
        assert 0.0 < partial < 1.0
        # not even valid JSON -> zero
        assert am.score_answer("USPS 41.75", gold) == 0.0

    def test_empty_prediction_scores_zero(self):
        assert am.score_answer("", "Shanghai villa") == 0.0
        assert am.score_answer("   ", "14.2") == 0.0


# -- answer rate (abstention) ----------------------------------------------

class TestAnswerRate:
    def test_attempt_vs_abstain(self):
        assert am.is_answered("CrossFit East River") is True
        assert am.is_answered("") is False
        assert am.is_answered("   ") is False
        assert am.is_answered("I don't know") is False
        assert am.is_answered("N/A") is False

    def test_answer_attempted_check(self):
        tc = _tc({"answer": "anything"})
        assert cc.answer_attempted(_trace("CrossFit East River"), tc) == 1.0
        assert cc.answer_attempted(_trace(""), tc) == 0.0
        assert cc.answer_attempted(_trace("I don't know"), tc) == 0.0

    def test_answer_rate_is_mean_attempted(self):
        # abstaining lowers answer rate but the abstained case scores 0 accuracy
        outputs = ["good answer", "", "another", "no answer"]
        rate = sum(am.is_answered(o) for o in outputs) / len(outputs)
        assert rate == 0.5

    def test_abstention_scores_zero_accuracy_not_partial(self):
        tc = _tc({"answer": "Shanghai villa"})
        assert cc.answer_accuracy(_trace(""), tc) == 0.0


# -- canonical check + catalog wiring --------------------------------------

class TestChecksAndCatalog:
    def test_checks_registered(self):
        assert "answer_accuracy" in CHECKS
        assert "answer_attempted" in CHECKS

    def test_answer_accuracy_check_reads_gold(self):
        tc = _tc({"answer": "1010000"})
        assert cc.answer_accuracy(_trace("1010000"), tc) == 1.0
        assert cc.answer_accuracy(_trace("0"), tc) == 0.0

    def test_metrics_in_catalog(self):
        assert BY_ID["answer_accuracy"].weight == pytest.approx(0.05)
        assert BY_ID["answer_accuracy"].category == "web_agent"
        # answer_rate is reported but UNWEIGHTED (not in the index)
        assert BY_ID["answer_rate"].weight == 0.0
        assert "2407.15711" in BY_ID["answer_accuracy"].methodology
        assert CHECK_TO_METRIC["answer_accuracy"] == "answer_accuracy"
        assert CHECK_TO_METRIC["answer_attempted"] == "answer_rate"

    def test_index_weights_sum_to_one_and_include_accuracy(self):
        w = index_weights()
        assert "answer_accuracy" in w
        assert "answer_rate" not in w          # unweighted -> not in index
        assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_answer_accuracy_feeds_index(self):
        vals = rollup_metrics_from_means({"answer_accuracy": 0.8})
        assert vals["answer_accuracy"] == 0.8
        out = compute_index(vals)
        assert "answer_accuracy" in out["components"]


# -- adapter: parses real records, scores, idempotent ----------------------

class TestAdapter:
    def test_parses_real_vendored_records(self):
        cases = AssistantBenchAdapter().load_records()      # vendored 16-record sample
        assert len(cases) == 16
        for c in cases:
            assert c.expected["source"] == "AssistantBench"
            assert c.expected["answer"]                       # gold present
            assert c.rubric_id == "assistantbench-v1-rubric"
            assert "web_agent" in c.tags

    def test_real_case_scores_through_engine(self, tmp_path):
        reg = Registry(tmp_path / "ab.db")
        summary = AssistantBenchAdapter().ingest(reg)
        assert summary["ingested"] == 16 and summary["license"] == "Apache-2.0"
        suite, cases = reg.get_suite("assistantbench-v1")
        rubric = reg.get_rubric("assistantbench-v1-rubric")
        c0 = cases[0]
        # an agent that emits the exact gold answer scores a perfect run
        good = score_run(_trace(c0.expected["answer"]), c0, rubric)
        assert good.scoring_error is None and good.passed is True
        # an agent that abstains attempts nothing -> not a pass
        bad = score_run(_trace(""), c0, rubric)
        assert bad.scoring_error is None and bad.passed is False

    def test_suite_labeled_real_dataset(self, tmp_path):
        reg = Registry(tmp_path / "ab.db")
        AssistantBenchAdapter().ingest(reg)
        suite, _ = reg.get_suite("assistantbench-v1")
        assert suite.approved is True
        assert "SEED SAMPLE" in suite.business_context  # default ingest = vendored sample
        assert "REAL public dataset" not in suite.business_context
        assert "Apache-2.0" in suite.business_context
        assert "assistantbench-v1" in DATASET_SUITE_IDS
        assert "assistantbench-v1" in canonical_suite_ids(reg)   # feeds the index

    def test_idempotent_ingest(self, tmp_path):
        reg = Registry(tmp_path / "ab.db")
        AssistantBenchAdapter().ingest(reg)
        again = AssistantBenchAdapter().ingest(reg)
        assert again["already_present"] is True
