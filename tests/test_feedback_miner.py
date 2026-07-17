"""SPEC-2 Step 13 — feedback → tests pipeline (closing the outer loop).

The miner turns stored human feedback (Step 11) into human-gated experience
data:

Acceptance criteria (one test each):
- a correction on a production trace yields a draft suite v(n+1) containing the
  mined case; it is unapproved; the original suite versions are untouched
  (still approved, same cases).
- ratings append valid rows to the calibration CSV (correct header + values).
- processed feedback is not mined twice (second mine_cases run mines nothing;
  already-processed corrections are skipped).
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone

import pytest

from agenttic.feedback import miner
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.feedback import HumanFeedback
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import Trace

T0 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
SUITE_ID = "support-suite"
AGENT_ID = "bot"
RUBRIC_ID = "support-rubric"


def _cfg(tmp_path) -> dict:
    return {"paths": {"review_dir": str(tmp_path / "review"),
                      "calibration_dir": str(tmp_path / "calibration")}}


def _seed_suite(reg, *, approved=True, n_cases=2) -> list[TestCase]:
    cases = [
        TestCase(test_id=f"{SUITE_ID}-c{i}", suite_id=SUITE_ID, version=1,
                 task_description=f"task {i}", input={"question": f"q{i}"},
                 expected={"final_output": f"a{i}"}, tags=["happy_path"],
                 rubric_id=RUBRIC_ID)
        for i in range(n_cases)
    ]
    suite = TestSuite(suite_id=SUITE_ID, version=1,
                      business_context="support desk", approved=False,
                      test_ids=[c.test_id for c in cases])
    reg.save_suite(suite, cases)
    if approved:
        reg.approve_suite(SUITE_ID, 1)
    return cases


def _prod_trace(reg, trace_id="tr-prod", test_case_id=f"{SUITE_ID}-c0") -> Trace:
    tr = Trace(trace_id=trace_id, agent_id=AGENT_ID, agent_config_hash="h",
               test_case_id=test_case_id, visibility="black_box",
               final_output="wrong answer", spans=[])
    reg.save_trace(tr, mode="live")
    return tr


def _correction(fid="fb-corr", *, trace_id="tr-prod",
                corrected="the correct answer") -> HumanFeedback:
    return HumanFeedback(
        feedback_id=fid, trace_id=trace_id, agent_id=AGENT_ID,
        source="end_user", kind="correction", corrected_output=corrected,
        rationale="agent gave the wrong refund policy", created_at=T0)


def _rating(fid="fb-rate", *, trace_id="tr-prod", criterion="tone",
            rating=1.0) -> HumanFeedback:
    return HumanFeedback(
        feedback_id=fid, trace_id=trace_id, agent_id=AGENT_ID,
        source="reviewer", kind="rating", criterion_id=criterion,
        rating=rating, rationale="polite and correct", created_at=T0)


# --------------------------------------------------------------------------- #
# Criterion 1: correction → draft v(n+1); unapproved; originals untouched.
# --------------------------------------------------------------------------- #


class TestMineCases:
    def test_correction_yields_unapproved_draft_next_version(self, tmp_path):
        reg = Registry(tmp_path / "m.db")
        orig_cases = _seed_suite(reg, approved=True)
        _prod_trace(reg)
        reg.save_feedback(_correction())

        mined = miner.mine_cases(reg, AGENT_ID, SUITE_ID, cfg=_cfg(tmp_path))

        # one mined case, carrying provenance + the corrected output as truth
        assert len(mined) == 1
        mc = mined[0]
        assert mc.version == 2
        assert mc.suite_id == SUITE_ID
        assert "mined_from_production" in mc.tags
        assert mc.expected == {"final_output": "the correct answer"}
        assert mc.rubric_id == RUBRIC_ID
        # originating test input recovered from the trace's test_case_id
        assert mc.input == {"question": "q0"}

        # a NEW draft version v2 exists, UNAPPROVED
        draft, draft_cases = reg.get_suite(SUITE_ID)  # latest = v2
        assert draft.version == 2
        assert draft.approved is False
        assert [c.test_id for c in draft_cases] == [mc.test_id]
        # provenance is preserved in the draft's business_context (Hard Rule 11)
        assert "fb-corr" in draft.business_context
        assert "tr-prod" in draft.business_context

        # ORIGINAL v1 is untouched: still approved, same cases
        v1, v1_cases = reg.get_suite(SUITE_ID, version=1)
        assert v1.approved is True
        assert [c.test_id for c in v1_cases] == [c.test_id for c in orig_cases]

        # a review file was written for the human gate
        review = tmp_path / "review" / f"{SUITE_ID}.md"
        assert review.exists()
        body = review.read_text()
        assert "DRAFT" in body and "approve" in body and "fb-corr" in body

    def test_no_corrections_is_noop(self, tmp_path):
        reg = Registry(tmp_path / "m.db")
        _seed_suite(reg, approved=True)
        # only a rating present — mine_cases must not create an empty v2
        _prod_trace(reg)
        reg.save_feedback(_rating())

        assert miner.mine_cases(reg, AGENT_ID, SUITE_ID, cfg=_cfg(tmp_path)) == []
        latest, _ = reg.get_suite(SUITE_ID)
        assert latest.version == 1  # no phantom draft version created


# --------------------------------------------------------------------------- #
# Criterion 2: ratings → valid calibration CSV rows (header + values).
# --------------------------------------------------------------------------- #


class TestMineLabels:
    def test_ratings_append_valid_rows(self, tmp_path):
        reg = Registry(tmp_path / "m.db")
        _seed_suite(reg, approved=True)
        _prod_trace(reg)
        reg.save_feedback(_rating("fb-r1", criterion="tone", rating=1.0))
        reg.save_feedback(_rating("fb-r2", trace_id="tr-prod",
                                  criterion="accuracy", rating=0.5))

        # route explicitly to SUITE_ID (no scorecard seeded)
        n = miner.mine_labels(reg, AGENT_ID, suite_id=SUITE_ID, cfg=_cfg(tmp_path))
        assert n == 2

        csv_path = tmp_path / "calibration" / f"{SUITE_ID}.csv"
        assert csv_path.exists()
        rows = list(csv.DictReader(csv_path.open()))
        assert [r for r in rows]  # header parsed correctly
        keyed = {(r["trace_id"], r["criterion_id"]): r["human_score"] for r in rows}
        assert keyed[("tr-prod", "tone")] == "1.0"
        assert keyed[("tr-prod", "accuracy")] == "0.5"

        # loadable by the calibration reader (correct header/format)
        from agenttic.scoring.calibration import load_labels
        labels = load_labels(csv_path)
        assert labels[("tr-prod", "accuracy")] == 0.5

    def test_rating_routed_via_scorecard_when_no_suite_arg(self, tmp_path):
        # A rating with no suite_id arg is routed to the suite the trace's
        # scorecard belongs to (derived from run_scores.trace_id).
        from agenttic.schema.scorecard import Scorecard, RunScore
        reg = Registry(tmp_path / "m.db")
        _seed_suite(reg, approved=True)
        _prod_trace(reg)
        reg.save_scorecard(Scorecard(
            scorecard_id="sc-1", agent_id=AGENT_ID, suite_id=SUITE_ID,
            suite_version=1, rubric_id=RUBRIC_ID, rubric_version=1,
            run_scores=[RunScore(trace_id="tr-prod", test_id=f"{SUITE_ID}-c0",
                                 criterion_scores=[], passed=True)],
            task_success_rate=1.0, mean_cost_usd=0.0, p95_latency_ms=0.0,
            visibility_tier="black_box"))
        reg.save_feedback(_rating("fb-r1", criterion="tone", rating=1.0))

        # no suite_id arg → derived from the scorecard
        assert miner.mine_labels(reg, AGENT_ID, cfg=_cfg(tmp_path)) == 1
        csv_path = tmp_path / "calibration" / f"{SUITE_ID}.csv"
        assert csv_path.exists()

    def test_second_run_does_not_duplicate_header_and_marks_processed(self, tmp_path):
        reg = Registry(tmp_path / "m.db")
        _seed_suite(reg, approved=True)
        _prod_trace(reg)
        reg.save_feedback(_rating("fb-r1", criterion="tone", rating=1.0))
        miner.mine_labels(reg, AGENT_ID, suite_id=SUITE_ID, cfg=_cfg(tmp_path))

        # a second rating arrives; re-mining appends its row, not a new header
        reg.save_feedback(_rating("fb-r2", criterion="accuracy", rating=0.0))
        assert miner.mine_labels(reg, AGENT_ID, suite_id=SUITE_ID,
                                 cfg=_cfg(tmp_path)) == 1

        csv_path = tmp_path / "calibration" / f"{SUITE_ID}.csv"
        lines = csv_path.read_text().strip().splitlines()
        assert lines[0] == "trace_id,criterion_id,human_score"
        assert sum(1 for line in lines if line.startswith("trace_id,")) == 1
        assert len(lines) == 3  # header + 2 data rows


# --------------------------------------------------------------------------- #
# Criterion 3: processed feedback is not mined twice.
# --------------------------------------------------------------------------- #


class TestIdempotent:
    def test_processed_corrections_not_mined_twice(self, tmp_path):
        reg = Registry(tmp_path / "m.db")
        _seed_suite(reg, approved=True)
        _prod_trace(reg)
        reg.save_feedback(_correction())

        first = miner.mine_cases(reg, AGENT_ID, SUITE_ID, cfg=_cfg(tmp_path))
        assert len(first) == 1

        # the correction is now marked processed → a second run mines nothing
        second = miner.mine_cases(reg, AGENT_ID, SUITE_ID, cfg=_cfg(tmp_path))
        assert second == []

        # and no phantom v3 was created (latest is still the v2 draft)
        latest, _ = reg.get_suite(SUITE_ID)
        assert latest.version == 2

    def test_ratings_not_mined_twice(self, tmp_path):
        reg = Registry(tmp_path / "m.db")
        _seed_suite(reg, approved=True)
        _prod_trace(reg)
        reg.save_feedback(_rating())

        assert miner.mine_labels(reg, AGENT_ID, suite_id=SUITE_ID,
                                 cfg=_cfg(tmp_path)) == 1
        assert miner.mine_labels(reg, AGENT_ID, suite_id=SUITE_ID,
                                 cfg=_cfg(tmp_path)) == 0

    def test_unroutable_rating_left_unprocessed(self, tmp_path):
        reg = Registry(tmp_path / "m.db")
        _seed_suite(reg, approved=True)
        _prod_trace(reg)
        reg.save_feedback(_rating())

        # no suite_id arg and no scorecard → cannot route → left unprocessed
        assert miner.mine_labels(reg, AGENT_ID, cfg=_cfg(tmp_path)) == 0
        assert {f.feedback_id for f in reg.unprocessed_feedback(AGENT_ID)} == {"fb-rate"}
        # a later, targeted run can still place it
        assert miner.mine_labels(reg, AGENT_ID, suite_id=SUITE_ID,
                                 cfg=_cfg(tmp_path)) == 1
