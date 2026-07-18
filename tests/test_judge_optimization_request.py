"""SPEC-3 Step 15.4 — auto-TRIGGER from the calibration flywheel.

When new human labels arrive, the platform NOTICES that a judge criterion needs
re-optimizing and FILES a request — but NEVER auto-runs the optimizer (the
analogue of Step 9's drift-triggered re-eval; optimization stays on-command via
``learn-judge``).

One test per acceptance criterion:

- test_agreement_drop_files_request_then_learn_clears_it
    new labels that drop a calibrated criterion below threshold create an OPEN
    request; running ``run_judge_learning`` for that criterion clears it
    (open -> cleared).
- test_request_is_never_auto_executed
    after ``mine_labels`` fires a request, NO optimization ran — no new active
    JudgeConfig version, no round record — only a request row exists.
- test_crossing_min_labels_files_request
    a criterion that crosses min_labels (was below, now at/above) gets a request.
- test_dedup_no_second_open_request
    mining MORE labels while a request is already open does not create a second
    open request for the same criterion.
- test_registry_round_trip
    save/open/clear round-trip for the request row.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from agenttic.feedback import miner
from agenttic.learning.judge_optimizer import run_judge_learning
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.feedback import HumanFeedback
from agenttic.schema.judge_config import seed_config_for
from agenttic.schema.judge_request import JudgeOptimizationRequest
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.trace import Span, Trace

NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)
CID = "tone"
AGENT = "agent-x"
SUITE = "support-suite"
RUBRIC = "r"

TONE = Criterion(
    criterion_id=CID, description="Professional, empathetic tone",
    scorer="judge", scale="binary",
    anchors={"pass": "Calm and specific.", "fail": "Sarcastic."})

# min_labels lowered so tests stay small; calibration threshold at the default.
CFG_MIN = 8
CFG = {
    "models": {"judge_strong": "judge-strong-model"},
    "paths": {},  # calibration_dir filled in per-tmp_path by _cfg()
    "judge_learning": {
        "min_labels": CFG_MIN, "holdout_frac": 0.4, "seed": 1234,
        "min_holdout_gain": 0.05, "max_overfit_gap": 0.15,
        "candidates_per_round": 1},
    "scoring": {"calibration_threshold": 0.8},
}

IMPROVED_MARKER = "IMPROVED_JUDGE_V2"


def _cfg(tmp_path) -> dict:
    cfg = json.loads(json.dumps(CFG))  # deep copy
    cfg["paths"] = {"review_dir": str(tmp_path / "review"),
                    "calibration_dir": str(tmp_path / "calibration")}
    return cfg


def _trace(tid: str, label: float) -> Trace:
    body = "GOOD tone" if label == 1.0 else "BAD tone"
    spans = [Span(span_id="f0", kind="final_output", name="final_output",
                  start_time=NOW, end_time=NOW)]
    return Trace(trace_id=tid, agent_id=AGENT, agent_config_hash="h",
                 test_case_id=tid, spans=spans, visibility="glass_box",
                 final_output=f"{body} :: {tid}")


def _save_scorecard(reg: Registry, tids: list[str], judge_score) -> None:
    """Persist one scorecard whose judge scores for CID are ``judge_score(tid)``
    per trace — these are the STORED judge numbers the agreement check reads."""
    runs = [
        RunScore(
            trace_id=tid, test_id=tid, passed=True,
            criterion_scores=[CriterionScore(
                criterion_id=CID, score=judge_score(tid), scorer="judge")])
        for tid in tids]
    sc = Scorecard.aggregate(
        scorecard_id=f"sc-{tids[0]}-{len(tids)}", agent_id=AGENT,
        suite_id=SUITE, suite_version=1, rubric_id=RUBRIC, rubric_version=1,
        run_scores=runs, visibility_tier="glass_box")
    reg.save_scorecard(sc)


def _rating(fid: str, tid: str, score: float) -> HumanFeedback:
    return HumanFeedback(
        feedback_id=fid, trace_id=tid, agent_id=AGENT, source="reviewer",
        kind="rating", criterion_id=CID, rating=score,
        rationale="human label", created_at=NOW)


def _seed_rubric(reg: Registry) -> None:
    reg.save_rubric(Rubric(rubric_id=RUBRIC, version=1, criteria=[TONE]))


def _mine(reg, tmp_path, feedbacks: list[HumanFeedback]) -> int:
    for fb in feedbacks:
        reg.save_feedback(fb)
    return miner.mine_labels(reg, AGENT, suite_id=SUITE, cfg=_cfg(tmp_path))


# --------------------------------------------------------------------------- #
# Mock judge/proposer — reused from the 15.3 pattern so a learning round runs
# with no network. The candidate (IMPROVED_MARKER) reads GOOD/BAD correctly.
# --------------------------------------------------------------------------- #

class FakeJudgeClient:
    def __init__(self):
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        system = kw.get("system", "")
        prompt = kw["messages"][0]["content"]
        tid = ""
        if " :: " in prompt:
            tid = prompt.split(" :: ", 1)[1].split("\n", 1)[0].strip()
        is_good = "GOOD tone" in prompt
        candidate = IMPROVED_MARKER in system
        score = (1.0 if is_good else 0.0) if candidate else 1.0
        verdict = json.dumps({"score": score, "rationale": f"tid={tid}"})
        return NS(content=[NS(type="text", text=verdict)],
                  usage=NS(input_tokens=5, output_tokens=5))


class FakeProposer:
    def __init__(self, judge_client):
        self.judge_client = judge_client

    def propose(self, dossier, active_config, n):
        return [{
            "system_prompt": active_config.system_prompt + " " + IMPROVED_MARKER,
            "changelog": "sharpen anchors from disagreements"}]


# --------------------------------------------------------------------------- #
# ACCEPTANCE 1 — agreement drop files a request; a learning round clears it.
# --------------------------------------------------------------------------- #

def test_agreement_drop_files_request_then_learn_clears_it(tmp_path):
    reg = Registry(tmp_path / "j.db")
    _seed_rubric(reg)

    # Phase 1: 8 GOOD-labeled traces. The stored judge OVER-scores (1.0 for
    # everyone) but every human label is 1.0 too -> perfect agreement. Mining
    # these crosses min_labels (8) so it files a "crossed" request; we clear it
    # to isolate the agreement-drop path in phase 2.
    good = [f"tr-{i}" for i in range(8)]
    for tid in good:
        reg.save_trace(_trace(tid, 1.0))
    _save_scorecard(reg, good, judge_score=lambda _tid: 1.0)
    _mine(reg, tmp_path, [_rating(f"fg-{i}", tid, 1.0)
                          for i, tid in enumerate(good)])
    assert "crossed min_labels" in reg.open_judge_optimization_requests(CID)[0].reason
    reg.clear_judge_optimization_requests(CID)

    # Phase 2: add 6 BAD-labeled traces (human 0.0) that the over-scoring judge
    # still calls 1.0 -> agreement now ~57% on 14 labels, below 0.80. The
    # criterion was ALREADY over min_labels (counts_before >= 8), so trigger 1
    # cannot fire; the agreement-drop trigger must.
    bad = [f"tr-{i}" for i in range(8, 14)]
    for tid in bad:
        reg.save_trace(_trace(tid, 0.0))
    _save_scorecard(reg, bad, judge_score=lambda _tid: 1.0)  # over-scores BAD
    _mine(reg, tmp_path, [_rating(f"fb-{i}", tid, 0.0)
                          for i, tid in enumerate(bad)])

    reqs = reg.open_judge_optimization_requests(CID)
    assert len(reqs) == 1
    assert reqs[0].status == "open"
    assert "dropped below threshold" in reqs[0].reason

    tids = good + bad
    human = {tid: (1.0 if tid in good else 0.0) for tid in tids}

    # Running the on-command optimizer for this criterion CLEARS the request.
    labels = {(tid, CID): human[tid] for tid in tids}
    reg.save_judge_config(seed_config_for(CID))
    jc = FakeJudgeClient()
    run_judge_learning(reg, _cfg(tmp_path), CID, rounds=1,
                       client=FakeProposer(jc), labels=labels)

    assert reg.open_judge_optimization_requests(CID) == []
    # the row is cleared (not deleted): round-trip via a fresh registry handle.
    reg2 = Registry(tmp_path / "j.db")
    assert reg2.open_judge_optimization_requests(CID) == []


# --------------------------------------------------------------------------- #
# ACCEPTANCE 2 — the request is NEVER auto-executed by mine_labels.
# --------------------------------------------------------------------------- #

def test_request_is_never_auto_executed(tmp_path):
    reg = Registry(tmp_path / "j.db")
    _seed_rubric(reg)
    reg.save_judge_config(seed_config_for(CID))  # active v1

    tids = [f"tr-{i}" for i in range(10)]
    human = {tid: (1.0 if i % 2 == 0 else 0.0) for i, tid in enumerate(tids)}
    for tid in tids:
        reg.save_trace(_trace(tid, human[tid]))
    _save_scorecard(reg, tids, judge_score=lambda _tid: 1.0)

    _mine(reg, tmp_path,
          [_rating(f"fb-{i}", tid, human[tid]) for i, tid in enumerate(tids)])

    # A request exists...
    assert len(reg.open_judge_optimization_requests(CID)) == 1
    # ...but NO optimization ran: still exactly one JudgeConfig (the seed v1),
    # no v2, and it is still the active config.
    lineage = reg.judge_lineage(CID)
    assert [c.version for c in lineage] == [1]
    assert reg.active_judge_config(CID).version == 1


# --------------------------------------------------------------------------- #
# ACCEPTANCE 3 — crossing min_labels files a request.
# --------------------------------------------------------------------------- #

def test_crossing_min_labels_files_request(tmp_path):
    reg = Registry(tmp_path / "j.db")
    _seed_rubric(reg)

    # First mine 5 labels (< min_labels=8): NOT optimizable -> no request.
    tids_a = [f"tr-{i}" for i in range(5)]
    for tid in tids_a:
        reg.save_trace(_trace(tid, 1.0))
    _mine(reg, tmp_path, [_rating(f"fa-{i}", tid, 1.0)
                          for i, tid in enumerate(tids_a)])
    assert reg.open_judge_optimization_requests(CID) == []

    # Now add 4 more -> 9 total, crossing min_labels (was below, now >=). A
    # request is filed even without any judge scores to compute agreement over.
    tids_b = [f"tr-{i}" for i in range(5, 9)]
    for tid in tids_b:
        reg.save_trace(_trace(tid, 1.0))
    _mine(reg, tmp_path, [_rating(f"fb-{i}", tid, 1.0)
                          for i, tid in enumerate(tids_b)])

    reqs = reg.open_judge_optimization_requests(CID)
    assert len(reqs) == 1
    assert "crossed min_labels" in reqs[0].reason
    assert "9 labels" in reqs[0].reason


# --------------------------------------------------------------------------- #
# ACCEPTANCE 4 — de-dup: a second mine while open doesn't stack a request.
# --------------------------------------------------------------------------- #

def test_dedup_no_second_open_request(tmp_path):
    reg = Registry(tmp_path / "j.db")
    _seed_rubric(reg)

    # Cross min_labels -> one open request.
    tids = [f"tr-{i}" for i in range(9)]
    for tid in tids:
        reg.save_trace(_trace(tid, 1.0))
    _mine(reg, tmp_path, [_rating(f"f1-{i}", tid, 1.0)
                          for i, tid in enumerate(tids)])
    first = reg.open_judge_optimization_requests(CID)
    assert len(first) == 1
    first_id = first[0].request_id

    # Mine MORE labels while the request is still open -> still exactly one open
    # request for the criterion (the existing row is refreshed, not stacked).
    more = [f"tr-{i}" for i in range(9, 14)]
    for tid in more:
        reg.save_trace(_trace(tid, 1.0))
    _mine(reg, tmp_path, [_rating(f"f2-{i}", tid, 1.0)
                          for i, tid in enumerate(more)])

    second = reg.open_judge_optimization_requests(CID)
    assert len(second) == 1
    assert second[0].request_id == first_id  # same row, refreshed in place


# --------------------------------------------------------------------------- #
# ACCEPTANCE 5 — registry round-trip for the request row.
# --------------------------------------------------------------------------- #

def test_registry_round_trip(tmp_path):
    reg = Registry(tmp_path / "j.db")
    req = JudgeOptimizationRequest(
        request_id="jor-1", criterion_id=CID, suite_id=SUITE,
        reason="criterion crossed min_labels (8 labels)")
    saved = reg.save_judge_optimization_request(req)
    assert saved.request_id == "jor-1" and saved.status == "open"

    # open list (all + filtered), then a second fresh handle sees it too.
    assert [r.request_id for r in reg.open_judge_optimization_requests()] == ["jor-1"]
    assert len(reg.open_judge_optimization_requests("other-crit")) == 0

    # de-dup at the registry level: a second open request for the same criterion
    # refreshes the existing one rather than inserting a duplicate.
    dup = JudgeOptimizationRequest(
        request_id="jor-2", criterion_id=CID, suite_id=SUITE,
        reason="agreement 0.60 dropped below threshold 0.80 on 12 labels")
    refreshed = reg.save_judge_optimization_request(dup)
    assert refreshed.request_id == "jor-1"  # same underlying open row
    open_now = reg.open_judge_optimization_requests(CID)
    assert len(open_now) == 1
    assert "dropped below threshold" in open_now[0].reason  # reason updated

    # clear marks it cleared (not deleted) and returns the count.
    assert reg.clear_judge_optimization_requests(CID) == 1
    assert reg.open_judge_optimization_requests(CID) == []
    # clearing again is a no-op.
    assert reg.clear_judge_optimization_requests(CID) == 0
