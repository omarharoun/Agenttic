"""SPEC-3 Step 15.3 — the judge optimization round (the judge analogue of
Step 14). Proposer + judge client are MOCKED; no network, no spend.

Each acceptance criterion maps to a test below:

- test_e2e_promotes_better_judge_and_lineage
    mocked e2e: an active judge that systematically OVER-scores `tone`; one round
    with a mocked proposer promotes a candidate that improves agreement on BOTH
    train and held-out; judge_lineage shows v1→v2 with both numbers.
- test_overfit_candidate_rejected
    candidate improves train but drops held-out beyond max_overfit_gap →
    rejected, reason contains "overfit".
- test_holdout_gain_below_margin_rejected
    candidate with held-out gain below min_holdout_gain → rejected (margin).
- test_holdout_error_fails_closed
    candidate that errors on a held-out case → rejected (fail closed).
- test_rejudge_new_version_original_untouched
    rejudge produces a NEW scorecard version; the original is untouched.
- test_fresh_judge_uses_v2_after_promotion
    after promotion, a fresh LLMJudge(reg=...) scoring call uses v2 automatically.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from agenttic.learning.judge_optimizer import (
    collect_disagreements,
    evaluate,
    gate,
    propose,
    rejudge,
    run_judge_learning,
)
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.judge_config import JudgeConfig, seed_config_for
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.trace import Span, Trace
from agenttic.scoring.calibration import frozen_split
from agenttic.scoring.judge import LLMJudge

NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)
CID = "tone"

CFG = {
    "models": {"judge_strong": "judge-strong-model", "agent_default": "agent-x"},
    "paths": {"calibration_dir": "calibration/"},
    "judge_learning": {
        "min_labels": 8, "holdout_frac": 0.4, "seed": 1234,
        "min_holdout_gain": 0.05, "max_overfit_gap": 0.15,
        "candidates_per_round": 1},
    "scoring": {},
}

# The candidate config carries this marker in its system prompt; the mock judge
# scores CORRECTLY only when it sees the marker (i.e. renders the candidate).
IMPROVED_MARKER = "IMPROVED_JUDGE_V2"

TONE = Criterion(
    criterion_id=CID, description="Professional, empathetic tone",
    scorer="judge", scale="binary",
    anchors={"pass": "Calm and specific.", "fail": "Sarcastic."})


# --------------------------------------------------------------------------- #
# Fixtures: a rubric, labeled traces (GOOD => human 1.0, BAD => human 0.0), and
# a persisted active seed config.
# --------------------------------------------------------------------------- #

def _trace(tid: str, label: float) -> Trace:
    body = "GOOD tone" if label == 1.0 else "BAD tone"
    spans = [Span(span_id="f0", kind="final_output", name="final_output",
                  start_time=NOW, end_time=NOW)]
    return Trace(trace_id=tid, agent_id="agent-x", agent_config_hash="h",
                 test_case_id=tid, spans=spans, visibility="glass_box",
                 final_output=f"{body} :: {tid}", total_cost_usd=0.0,
                 total_steps=0)


def _seed(reg: Registry, n: int = 10):
    """Save a rubric, n labeled traces (alternating GOOD/BAD), the active seed
    judge config, and return (labels, trace_ids)."""
    reg.save_rubric(Rubric(rubric_id="r", version=1, criteria=[TONE]))
    labels: dict = {}
    tids: list[str] = []
    for i in range(n):
        label = 1.0 if i % 2 == 0 else 0.0
        tid = f"tr-{i}"
        reg.save_trace(_trace(tid, label))
        labels[(tid, CID)] = label
        tids.append(tid)
    reg.save_judge_config(seed_config_for(CID))
    return labels, tids


# --------------------------------------------------------------------------- #
# Mock judge client: the score depends on which config renders the prompt.
#
# The active/seed judge OVER-scores: it returns 1.0 for every trace (so it is
# right on GOOD traces, wrong on BAD ones -> ~50% agreement). The candidate,
# whose system prompt carries IMPROVED_MARKER, reads the GOOD/BAD evidence out of
# the fence and scores correctly (perfect agreement). ``error_on`` lets a test
# force a judge failure on specific traces (fail-closed coverage).
# --------------------------------------------------------------------------- #

class FakeJudgeClient:
    def __init__(self, *, error_on: set[str] | None = None,
                 candidate_holdout_wrong: set[str] | None = None):
        self.error_on = error_on or set()
        # trace_ids the candidate should get WRONG on the held-out split, to
        # simulate overfitting / a sub-margin gain.
        self.candidate_holdout_wrong = candidate_holdout_wrong or set()
        self.calls: list[dict] = []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        system = kw.get("system", "")
        prompt = kw["messages"][0]["content"]
        self.calls.append({"system": system, "prompt": prompt})
        # recover the trace id from the evidence body ("... :: tr-N")
        tid = ""
        if " :: " in prompt:
            tid = prompt.split(" :: ", 1)[1].split("\n", 1)[0].strip()
        if tid in self.error_on:
            raise RuntimeError(f"forced judge failure on {tid}")
        is_good = "GOOD tone" in prompt
        candidate = IMPROVED_MARKER in system
        if candidate:
            # candidate reads evidence correctly, EXCEPT where a test forces it
            # to be wrong on the held-out split.
            true_score = 1.0 if is_good else 0.0
            if tid in self.candidate_holdout_wrong:
                score = 0.0 if true_score == 1.0 else 1.0
            else:
                score = true_score
        else:
            score = 1.0  # active seed judge OVER-scores everything
        verdict = json.dumps({"score": score, "rationale": f"tid={tid}"})
        return NS(content=[NS(type="text", text=verdict)],
                  usage=NS(input_tokens=5, output_tokens=5))


class FakeProposer:
    """Injected proposer: yields ONE candidate config with the improved-judge
    marker in its system prompt (and a few-shot example drawn from a
    disagreement). ``few_shot`` may be tweaked per test."""

    def __init__(self, judge_client, *, system_suffix: str = ""):
        self.judge_client = judge_client
        self.system_suffix = system_suffix
        self.last_dossier = None

    def propose(self, dossier, active_config, n):
        self.last_dossier = dossier
        example = (dossier["disagreements"][0] if dossier["disagreements"]
                   else {"trace_excerpt": "x", "human_score": 0.0,
                         "rationale": "r"})
        return [{
            "system_prompt": (active_config.system_prompt + " " + IMPROVED_MARKER
                              + " " + self.system_suffix),
            "few_shot_examples": [{
                "trace_excerpt": example["trace_excerpt"],
                "human_score": example["human_score"],
                "rationale": "human ground truth"}],
            "changelog": "sharpen anchors + few-shot from disagreements",
        }]


# --------------------------------------------------------------------------- #
# ACCEPTANCE 1 — mocked e2e: promotes a better judge; lineage shows v1->v2.
# --------------------------------------------------------------------------- #

def test_e2e_promotes_better_judge_and_lineage(tmp_path):
    reg = Registry(tmp_path / "j.db")
    labels, _ = _seed(reg, n=10)
    jc = FakeJudgeClient()
    proposer = FakeProposer(jc)

    summary = run_judge_learning(reg, CFG, CID, rounds=1, client=proposer,
                                 labels=labels)

    assert summary["refused"] is False
    assert len(summary["promoted"]) == 1
    promoted = summary["promoted"][0]
    assert promoted.status == "active"
    assert promoted.version == 2
    assert IMPROVED_MARKER in promoted.system_prompt

    # judge_lineage shows v1 -> v2; v1 retired, v2 active.
    lineage = reg.judge_lineage(CID)
    versions = {c.version: c.status for c in lineage}
    assert versions[1] == "retired"
    assert versions[2] == "active"

    # The promotion round record carries train + held-out numbers (v1->v2),
    # and the candidate strictly improved BOTH.
    rec = next(r for r in summary["records"]
               if r.candidate_id == promoted.judge_config_id and r.promoted)
    assert rec.train_after > rec.train_before
    assert rec.holdout_after is not None and rec.holdout_before is not None
    assert rec.holdout_after > rec.holdout_before
    assert rec.n_holdout_scored > 0
    # The lineage changelog embeds the numbers (queryable).
    assert "round=" in promoted.changelog
    embedded = json.loads(promoted.changelog.split("round=", 1)[1])
    assert embedded["train"][1] > embedded["train"][0]


# --------------------------------------------------------------------------- #
# ACCEPTANCE 2 — overfit: improves train, drops held-out beyond max_overfit_gap.
# --------------------------------------------------------------------------- #

def test_overfit_candidate_rejected(tmp_path):
    reg = Registry(tmp_path / "j.db")
    labels, _ = _seed(reg, n=10)
    split = frozen_split(reg, labels, CID, holdout_frac=0.4, seed=1234)
    _, holdout = split
    holdout_ids = sorted({t for (t, _c) in holdout})

    # Candidate is PERFECT on train but wrong on ONE held-out case. Active
    # over-scores -> its held-out agreement is ~0.5. Candidate held-out clears
    # the margin over active but still trails its own PERFECT train by more than
    # max_overfit_gap (0.15) -> rejected as overfit (not as a margin miss).
    jc = FakeJudgeClient(candidate_holdout_wrong={holdout_ids[0]})
    proposer = FakeProposer(jc)

    active_eval = evaluate(seed_config_for(CID), reg, TONE, split, labels,
                           client=jc, cfg=CFG)
    dossier = collect_disagreements(reg, CID, split, labels, cfg=CFG, client=jc,
                                    criterion=TONE)
    cand = propose(dossier, seed_config_for(CID), 1, client=proposer)[0]
    cand_eval = evaluate(cand, reg, TONE, split, labels, client=jc, cfg=CFG)

    promote_ok, reason = gate(cand_eval, active_eval, CFG)
    assert promote_ok is False
    assert "overfit" in reason.lower()


# --------------------------------------------------------------------------- #
# ACCEPTANCE 3 — held-out gain below min_holdout_gain (margin enforced).
# --------------------------------------------------------------------------- #

def test_holdout_gain_below_margin_rejected(tmp_path):
    reg = Registry(tmp_path / "j.db")
    labels, _ = _seed(reg, n=10)
    split = frozen_split(reg, labels, CID, holdout_frac=0.4, seed=1234)

    jc = FakeJudgeClient()
    proposer = FakeProposer(jc)
    active_eval = evaluate(seed_config_for(CID), reg, TONE, split, labels,
                           client=jc, cfg=CFG)
    dossier = collect_disagreements(reg, CID, split, labels, cfg=CFG, client=jc,
                                    criterion=TONE)
    cand = propose(dossier, seed_config_for(CID), 1, client=proposer)[0]
    cand_eval = evaluate(cand, reg, TONE, split, labels, client=jc, cfg=CFG)

    # Directly gate with a tiny held-out gain (below the 0.05 margin) but a real
    # train improvement — the margin must veto it.
    active_like = dict(active_eval)
    candidate_like = dict(cand_eval)
    candidate_like["holdout_agreement"] = (active_eval["holdout_agreement"] or 0.0) + 0.01
    candidate_like["train_agreement"] = (active_eval["train_agreement"] or 0.0) + 0.2
    candidate_like["n_holdout_scored"] = max(1, cand_eval["n_holdout_scored"])
    promote_ok, reason = gate(candidate_like, active_like, CFG)
    assert promote_ok is False
    assert "margin" in reason.lower() or "min_holdout_gain" in reason


# --------------------------------------------------------------------------- #
# ACCEPTANCE 4 — candidate errors on a held-out case => fail closed.
# --------------------------------------------------------------------------- #

def test_holdout_error_fails_closed(tmp_path):
    reg = Registry(tmp_path / "j.db")
    labels, _ = _seed(reg, n=10)
    split = frozen_split(reg, labels, CID, holdout_frac=0.4, seed=1234)
    _, holdout = split
    holdout_ids = {t for (t, _c) in holdout}

    # Force the judge to FAIL on every held-out trace -> zero held-out scored.
    jc = FakeJudgeClient(error_on=holdout_ids)
    proposer = FakeProposer(jc)
    active_eval = evaluate(seed_config_for(CID), reg, TONE, split, labels,
                           client=FakeJudgeClient(), cfg=CFG)
    dossier = collect_disagreements(reg, CID, split, labels, cfg=CFG,
                                    client=FakeJudgeClient(), criterion=TONE)
    cand = propose(dossier, seed_config_for(CID), 1, client=proposer)[0]
    cand_eval = evaluate(cand, reg, TONE, split, labels, client=jc, cfg=CFG)

    assert cand_eval["n_holdout_scored"] == 0
    assert cand_eval["errors"]
    promote_ok, reason = gate(cand_eval, active_eval, CFG)
    assert promote_ok is False
    assert "fail closed" in reason.lower() or "no held-out" in reason.lower()


# --------------------------------------------------------------------------- #
# ACCEPTANCE 5 — rejudge produces a NEW scorecard; the original is untouched.
# --------------------------------------------------------------------------- #

def test_rejudge_new_version_original_untouched(tmp_path):
    reg = Registry(tmp_path / "j.db")
    _seed(reg, n=4)
    # Build an original scorecard whose stored tone scores are ALL 1.0 (the old
    # over-scoring judge). It has a mix of GOOD (tr-0/tr-2) and BAD (tr-1/tr-3).
    runs = []
    for i in range(4):
        runs.append(RunScore(
            trace_id=f"tr-{i}", test_id=f"tr-{i}", passed=True,
            criterion_scores=[CriterionScore(criterion_id=CID, score=1.0,
                                             scorer="judge",
                                             judge_rationale="old")]))
    original = Scorecard.aggregate(
        scorecard_id="sc-original", agent_id="agent-x", suite_id="s",
        suite_version=1, rubric_id="r", rubric_version=1, run_scores=runs,
        visibility_tier="glass_box")
    reg.save_scorecard(original)

    # Promote a correct judge to active so rejudge uses it (via a full round).
    labels = {(f"tr-{i}", CID): (1.0 if i % 2 == 0 else 0.0) for i in range(4)}
    jc = FakeJudgeClient()
    # min_labels small enough for n=4
    cfg = {**CFG, "judge_learning": {**CFG["judge_learning"], "min_labels": 4}}
    run_judge_learning(reg, cfg, CID, rounds=1, client=FakeProposer(jc),
                       labels=labels)
    assert reg.active_judge_config(CID).version == 2

    new_sc = rejudge(reg, cfg, "sc-original", client=jc)

    # NEW id, both scorecards queryable, original bytes unchanged.
    assert new_sc.scorecard_id != original.scorecard_id
    reread_original = reg.get_scorecard("sc-original")
    assert all(cs.score == 1.0 for r in reread_original.run_scores
               for cs in r.criterion_scores)  # untouched
    # The re-judged scores now reflect the corrected judge: GOOD=1.0, BAD=0.0.
    scored = {r.trace_id: r.criterion_scores[0].score for r in new_sc.run_scores}
    assert scored["tr-0"] == 1.0 and scored["tr-2"] == 1.0
    assert scored["tr-1"] == 0.0 and scored["tr-3"] == 0.0


# --------------------------------------------------------------------------- #
# ACCEPTANCE 6 — after promotion, a fresh LLMJudge(reg=...) uses v2 automatically.
# --------------------------------------------------------------------------- #

def test_fresh_judge_uses_v2_after_promotion(tmp_path):
    reg = Registry(tmp_path / "j.db")
    labels, _ = _seed(reg, n=10)
    jc = FakeJudgeClient()
    run_judge_learning(reg, CFG, CID, rounds=1, client=FakeProposer(jc),
                       labels=labels)
    assert reg.active_judge_config(CID).version == 2

    # A brand-new judge built with just the registry handle must render v2 — so
    # its system prompt carries the improved marker on a fresh scoring call.
    fresh_client = FakeJudgeClient()
    judge = LLMJudge(model="judge-strong-model", agent_model="agent-x",
                     client=fresh_client, cfg=CFG, reg=reg)
    bad = _trace("tr-1", 0.0)  # a BAD trace the old judge over-scored as 1.0
    from agenttic.schema.testcase import TestCase
    tc = TestCase(test_id="tr-1", suite_id="s", task_description="t",
                  input={}, rubric_id="r")
    score = judge.score_criterion(TONE, bad, tc)
    # v2 renders the improved marker in the system prompt and scores correctly.
    assert IMPROVED_MARKER in fresh_client.calls[0]["system"]
    assert score.score == 0.0


# --------------------------------------------------------------------------- #
# Guard — refuses below min_labels.
# --------------------------------------------------------------------------- #

def test_run_refuses_below_min_labels(tmp_path):
    reg = Registry(tmp_path / "j.db")
    labels, _ = _seed(reg, n=4)  # below min_labels=8
    summary = run_judge_learning(reg, CFG, CID, rounds=1,
                                 client=FakeProposer(FakeJudgeClient()),
                                 labels=labels)
    assert summary["refused"] is True
    assert "insufficient labels" in summary["reason"]
    assert summary["promoted"] == []


# --------------------------------------------------------------------------- #
# propose CONSTRAINT — candidate cannot change scale/description (structural).
# --------------------------------------------------------------------------- #

def test_propose_cannot_change_scale_or_description():
    active = seed_config_for(CID)
    proposer = FakeProposer(FakeJudgeClient())
    dossier = {"criterion_id": CID, "scale": "binary", "description": "d",
               "anchors": {}, "disagreements": []}
    cands = propose(dossier, active, 1, client=proposer)
    cand = cands[0]
    # JudgeConfig has no scale/description fields at all -> structurally immutable.
    assert not hasattr(cand, "scale")
    assert not hasattr(cand, "description")
    assert cand.criterion_id == active.criterion_id
    assert cand.version == active.version + 1
    assert cand.parent_id == active.judge_config_id
    assert cand.status == "candidate"


# --------------------------------------------------------------------------- #
# rejected candidates are queryable with reasons (auditable search history).
# --------------------------------------------------------------------------- #

def test_rejected_candidate_recorded_with_reason(tmp_path):
    reg = Registry(tmp_path / "j.db")
    labels, _ = _seed(reg, n=10)
    split = frozen_split(reg, labels, CID, holdout_frac=0.4, seed=1234)
    _, holdout = split
    holdout_ids = {t for (t, _c) in holdout}
    # Candidate errors on held-out -> fail closed -> a rejected round.
    jc = FakeJudgeClient(error_on=holdout_ids)
    proposer = FakeProposer(jc)
    summary = run_judge_learning(reg, CFG, CID, rounds=1, client=proposer,
                                 judge_client=jc, labels=labels)
    assert summary["promoted"] == []
    assert len(summary["rejected"]) >= 1
    # The rejected config is persisted in the lineage with status="rejected".
    lineage = {c.judge_config_id: c for c in reg.judge_lineage(CID)}
    rejected_cfgs = [c for c in lineage.values() if c.status == "rejected"]
    assert rejected_cfgs
    assert any("round=" in c.changelog for c in rejected_cfgs)
