"""SPEC-7 Step 31 — pass^k reliability: estimator, curve, gate, cert floor."""

from __future__ import annotations

import random
from math import comb
from types import SimpleNamespace

import pytest

import agenttic.learning.optimizer as gate_mod
from agenttic.learning.optimizer import gate
from agenttic.reliability import (
    ReliabilityError, flakiness_gap, is_certification_grade, pass_hat_k,
    pass_k_curve, pass_k_regression, require_certification_grade,
)
from agenttic.reporting.scorecard_report import render_markdown
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard


# --- 1. estimator ---------------------------------------------------------- #
def test_estimator_matches_hand_computed_and_degenerate():
    assert pass_hat_k(6, 8, 4) == pytest.approx(comb(6, 4) / comb(8, 4))  # 15/70
    assert pass_hat_k(0, 8, 4) == 0.0          # j < k' -> 0
    assert pass_hat_k(8, 8, 4) == 1.0          # all pass -> 1
    assert pass_hat_k(3, 8, 4) == 0.0          # still < k'
    with pytest.raises(ValueError):
        pass_hat_k(4, 4, 5)                     # k' > k


# --- 2. flaky stub agent (50% per trial) ----------------------------------- #
def test_flaky_agent_pass1_half_pass4_low():
    rng = random.Random(7)
    # 300 cases, each run k=8 times; a "flaky stub" passes each trial ~50%
    trials = {f"c{i}": [rng.random() < 0.5 for _ in range(8)] for i in range(300)}
    curve = pass_k_curve(trials)
    assert curve[1] == pytest.approx(0.5, abs=0.05)      # pass^1 ~ 0.5
    assert curve[4] == pytest.approx(0.0625, abs=0.03)   # pass^4 ~ 0.5^4
    assert flakiness_gap(curve) > 0.3                     # a wide flakiness gap


# --- 3. scorecard aggregation --------------------------------------------- #
def _sc(passes_per_case: dict[str, list[bool]], agent_id="a"):
    runs = []
    for tid, verdicts in passes_per_case.items():
        for j, ok in enumerate(verdicts):
            runs.append(RunScore(trace_id=f"{tid}-{j}", test_id=tid,
                        criterion_scores=[CriterionScore(criterion_id="m",
                                          score=1.0 if ok else 0.0, scorer="code")],
                        passed=ok))
    return Scorecard.aggregate(scorecard_id=f"sc-{agent_id}", agent_id=agent_id,
                               suite_id="s", suite_version=1, rubric_id="rb",
                               rubric_version=1, run_scores=runs, visibility_tier="glass_box")


def test_scorecard_aggregates_pass_k_curve():
    sc = _sc({"c0": [True, True, True, False], "c1": [True, False, False, False]})
    assert sc.trials_per_case == 4
    assert sc.pass_k_curve is not None and 4 in sc.pass_k_curve
    # pass^1 = mean(3/4, 1/4) = 0.5
    assert sc.pass_k_curve[1] == pytest.approx(0.5)
    # single-trial scorecard has no curve
    assert _sc({"c0": [True]}).pass_k_curve is None


# --- 4. report rendering --------------------------------------------------- #
def test_report_renders_reliability_and_flakiness_gap():
    sc = _sc({"c0": [True, True, False, False], "c1": [True, True, True, False]})
    rubric = Rubric(rubric_id="rb", version=1, weights={"m": 1.0}, criteria=[
        Criterion(criterion_id="m", description="d", scorer="code", scale="binary",
                  check_ref="final_output_matches_expected")])
    md = render_markdown(sc, rubric)
    assert "Reliability (pass^k)" in md and "flakiness gap" in md

    single = _sc({"c0": [True]})
    assert "single-trial" in render_markdown(single, rubric)


# --- 5. gate's optional pass^k mode ---------------------------------------- #
def test_pass_k_regression_helper():
    base = SimpleNamespace(pass_k_curve={1: 0.5, 8: 0.45})
    worse = SimpleNamespace(pass_k_curve={1: 0.7, 8: 0.30})   # pass^1 up, pass^8 down
    assert "reliability regressed" in pass_k_regression(base, worse)
    better = SimpleNamespace(pass_k_curve={1: 0.7, 8: 0.50})
    assert pass_k_regression(base, better) is None


def test_gate_rejects_flakiness_regression_when_enabled(monkeypatch):
    # a comparison the deterministic gate accepts (pass rate up, no sig regress)
    comp = SimpleNamespace(success_rate_a=0.6, success_rate_b=0.8, success_delta=0.2,
                           n_paired=20, per_criterion=[])
    monkeypatch.setattr(gate_mod, "compare_scorecards", lambda *a, **k: comp)
    base = SimpleNamespace(per_criterion_means={}, mean_cost_usd=0.01, p95_latency_ms=100,
                           agent_id="b", pass_k_curve={1: 0.5, 8: 0.45})
    cand = SimpleNamespace(per_criterion_means={}, mean_cost_usd=0.01, p95_latency_ms=100,
                           agent_id="c", pass_k_curve={1: 0.7, 8: 0.30})
    promote, reason = gate(cand, base, {"learning": {"gate_on_pass_k": True}})
    assert not promote and "reliability regressed" in reason
    # without the flag, the same promotion is accepted
    promote2, _ = gate(cand, base, {"learning": {}})
    assert promote2


# --- 6. certification reliability floor ------------------------------------ #
def test_cert_reliability_floor():
    assert is_certification_grade(4) and not is_certification_grade(2)
    require_certification_grade(4)          # ok
    with pytest.raises(ReliabilityError):
        require_certification_grade(2)
