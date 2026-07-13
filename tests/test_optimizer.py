"""Prompt-optimizer — the self-improving system-prompt loop, with the LLM
mocked (no real spend):

- the suite is split into train/heldout deterministically and the splits are
  disjoint (the overfitting guard: the optimizer never sees held-out cases);
- reflection extracts the failing criteria + judge rationales (the gradient),
  excluding passed/errored runs;
- a candidate that improves the pass rate with no regressed criterion is
  ACCEPTED; one that significantly regresses a criterion is REJECTED even when
  its overall pass rate is higher (regression protection);
- the held-out score is reported for baseline and best (train-vs-heldout makes
  overfitting visible);
- total suite executions stay within the projected/cap bound (cost is bounded);
- the run + prompt lineage persist and round-trip through the registry.

The agent/judge are never called: ``_score_prompt`` is stubbed to return
deterministic scorecards, and a fake optimizer proposes fixed candidates.
"""

import asyncio
import uuid

import pytest

from agenttic import optimizer as optmod
from agenttic.optimizer import (
    evaluate_candidate,
    project_runs,
    reflect_on_failures,
    split_suite,
)
from agenttic.ab import compare_scorecards
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.ab import ABVariant
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.testcase import TestCase, TestSuite

CFG = {
    "models": {"agent_default": "agent-model", "judge_strong": "judge-model",
               "judge_light": "judge-light"},
    "harness": {"timeout_seconds": 10, "max_parallel": 5,
                "transport_retries": 1, "max_steps": 10},
    "scoring": {"calibration_threshold": 0.8},
    "paths": {"review_dir": "review/"},
    "budget": {},
    "security": {},
}


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "opt.db")


def _seed_suite(reg: Registry, n: int = 6) -> str:
    """A small approved suite (n cases) + a 2-criterion rubric."""
    reg.save_rubric(Rubric(
        rubric_id="r", version=1,
        criteria=[
            Criterion(criterion_id="acc", description="answers correctly",
                      scorer="judge", scale="binary",
                      anchors={"pass": "right", "fail": "wrong"}),
            Criterion(criterion_id="tone", description="is polite",
                      scorer="judge", scale="binary",
                      anchors={"pass": "polite", "fail": "rude"}),
        ]))
    cases = [TestCase(test_id=f"tc-{i}", suite_id="s-opt", version=1,
                      task_description=f"task {i}", input={"x": i}, rubric_id="r")
             for i in range(n)]
    reg.save_suite(TestSuite(suite_id="s-opt", version=1, business_context="x",
                             test_ids=[c.test_id for c in cases], approved=True),
                   cases)
    return "s-opt"


def _card(reg, suite_id, behavior, agent_id="agent-under-test"):
    """Build + persist a scorecard for every case of ``suite_id``. ``behavior``
    maps test_id -> (passed, {criterion: score})."""
    _suite, cases = reg.get_suite(suite_id)
    runs = []
    for c in cases:
        passed, crit = behavior(c.test_id)
        cs = [CriterionScore(criterion_id=k, score=v, scorer="judge",
                             judge_rationale=f"{k} was {v}")
              for k, v in crit.items()]
        runs.append(RunScore(trace_id=f"tr-{uuid.uuid4().hex[:8]}",
                             test_id=c.test_id, criterion_scores=cs,
                             passed=passed, cost_usd=0.01, scoring_cost_usd=0.002))
    sc = Scorecard.aggregate(
        scorecard_id=uuid.uuid4().hex[:12], agent_id=agent_id, suite_id=suite_id,
        suite_version=1, rubric_id="r", rubric_version=1, run_scores=runs,
        visibility_tier="glass_box")
    reg.save_scorecard(sc)
    return sc


# -- split (overfitting guard) -----------------------------------------------

class TestSplit:
    def test_deterministic_and_disjoint(self):
        ids = [f"tc-{i}" for i in range(10)]
        tr1, ho1 = split_suite(ids, 0.3, seed=1234)
        tr2, ho2 = split_suite(ids, 0.3, seed=1234)
        assert (tr1, ho1) == (tr2, ho2)             # deterministic
        assert set(tr1) & set(ho1) == set()         # disjoint
        assert set(tr1) | set(ho1) == set(ids)      # a partition
        assert len(ho1) == 3 and len(tr1) == 7

    def test_keeps_train_nonempty(self):
        tr, ho = split_suite(["a", "b"], 0.9, seed=1)
        assert len(tr) >= 1 and len(ho) >= 1        # never empties train
        tr1, ho1 = split_suite(["only"], 0.5, seed=1)
        assert tr1 == ["only"] and ho1 == []        # nothing to hold out


# -- reflection (the textual gradient) ---------------------------------------

class TestReflect:
    def test_extracts_failing_criteria_and_rationales(self, reg):
        suite_id = _seed_suite(reg, n=4)
        _suite, cases = reg.get_suite(suite_id)
        rubric = reg.get_rubric("r")

        def behavior(tid):
            if tid in ("tc-0", "tc-1"):
                return False, {"acc": 0.0, "tone": 1.0}   # fail acc
            return True, {"acc": 1.0, "tone": 1.0}
        sc = _card(reg, suite_id, behavior)
        # add an errored run — must be ignored
        sc.run_scores.append(RunScore(trace_id="tr-e", test_id="tc-err",
                                      criterion_scores=[], passed=False,
                                      scoring_error="boom"))

        refl = reflect_on_failures(sc, rubric, cases)
        assert refl["n_failing"] == 2
        assert refl["failing_criteria"] == ["acc"]        # only acc was missed
        acc = refl["per_criterion"][0]
        assert acc["criterion_id"] == "acc" and acc["n_failed"] == 2
        assert acc["rationales"]                           # carries judge text


# -- acceptance (regression protection) --------------------------------------

def _sc(scid, passes, crit):
    runs = [RunScore(trace_id=f"tr-{scid}-{t}", test_id=t,
                     criterion_scores=[CriterionScore(criterion_id=c, score=v,
                                                      scorer="code")
                                       for c, v in crit[t].items()],
                     passed=passes[t], cost_usd=0.01)
            for t in sorted(passes)]
    return Scorecard.aggregate(scorecard_id=scid, agent_id="a", suite_id="s",
                               suite_version=1, rubric_id="r", rubric_version=1,
                               run_scores=runs, visibility_tier="glass_box")


def _cmp(base, cand):
    return compare_scorecards("c", base, cand,
                              ABVariant(label="A", agent_id="a"),
                              ABVariant(label="B", agent_id="a"))


class TestEvaluate:
    def test_accepts_clean_improvement(self):
        ids = [f"t{i}" for i in range(4)]
        base = _sc("b", {t: False for t in ids}, {t: {"acc": 0.0} for t in ids})
        cand = _sc("c", {t: True for t in ids}, {t: {"acc": 1.0} for t in ids})
        accept, regs, reason = evaluate_candidate(_cmp(base, cand))
        assert accept and not regs and "accepted" in reason

    def test_rejects_significant_regression_even_if_pass_rate_up(self):
        ids = [f"t{i}" for i in range(4)]
        # candidate passes MORE cases (success_delta>0) but tanks 'tone' on all
        base = _sc("b", {t: (t in ("t0", "t1")) for t in ids},
                   {t: {"acc": 0.0, "tone": 1.0} for t in ids})
        cand = _sc("c", {t: True for t in ids},
                   {t: {"acc": 1.0, "tone": 0.0} for t in ids})
        comp = _cmp(base, cand)
        assert comp.success_delta > 0                      # overall pass rate up
        accept, regs, reason = evaluate_candidate(comp)
        assert not accept                                  # ...but vetoed
        assert any(r.criterion_id == "tone" for r in regs)
        assert "regress" in reason

    def test_rejects_no_gain(self):
        ids = [f"t{i}" for i in range(4)]
        base = _sc("b", {t: True for t in ids}, {t: {"acc": 1.0} for t in ids})
        cand = _sc("c", {t: True for t in ids}, {t: {"acc": 1.0} for t in ids})
        accept, regs, reason = evaluate_candidate(_cmp(base, cand))
        assert not accept and "no pass-rate improvement" in reason


# -- cost projection ----------------------------------------------------------

def test_project_runs_bound():
    assert project_runs(2, 3, has_heldout=True) == 2 + 2 * (4 + 1)
    assert project_runs(1, 1, has_heldout=False) == 1 + 1 * 2


# -- the loop (stubbed scoring + fake optimizer) -----------------------------

class FakeOptimizer:
    """Proposes fixed candidate prompts; records zero cost."""
    def __init__(self, candidates):
        self._candidates = candidates
        self.last_cost_usd = 0.0
        self.seen_reflections = []

    def propose(self, current_prompt, reflection, n):
        self.seen_reflections.append(reflection)
        return self._candidates[:n]


def _patch_score(monkeypatch, behavior_for):
    """Stub _score_prompt to return a deterministic scorecard chosen by the
    system prompt + which split (suite_id) it's run on."""
    async def fake(cfg, reg, agent_id, system_prompt, suite_id, **kw):
        return _card(reg, suite_id,
                     lambda tid: behavior_for(system_prompt, suite_id, tid),
                     agent_id=agent_id)
    monkeypatch.setattr(optmod, "_score_prompt", fake)


class TestOptimizeLoop:
    def test_accepts_improver_reports_heldout_and_persists(self, reg, monkeypatch):
        suite_id = _seed_suite(reg, n=6)

        def behavior_for(prompt, suite_id_, tid):
            good = "GOOD" in prompt
            if good:
                return True, {"acc": 1.0, "tone": 1.0}      # candidate fixes all
            # baseline: half the cases fail 'acc'
            fail = tid in ("tc-0", "tc-1", "tc-2")
            return (not fail), {"acc": 0.0 if fail else 1.0, "tone": 1.0}
        _patch_score(monkeypatch, behavior_for)
        fake = FakeOptimizer([{"prompt": "GOOD PROMPT", "rationale": "fix acc"}])

        run = asyncio.run(optmod.optimize(
            CFG, reg, "agent-under-test", suite_id, rounds=1,
            candidates_per_round=1, heldout_fraction=0.34, seed=7,
            baseline_prompt="BASE", optimizer=fake, max_agent_runs=60))

        # improvement adopted
        assert run.improved and run.best_version == 1
        assert run.best_prompt == "GOOD PROMPT"
        assert run.best_train_rate > run.baseline_train_rate
        # held-out reported for baseline AND best (the overfitting guard)
        assert run.baseline_heldout_rate is not None
        assert run.best_heldout_rate is not None
        assert run.overfit_gap is not None
        # splits are disjoint and the optimizer only ever saw train cases
        assert set(run.train_test_ids) & set(run.heldout_test_ids) == set()
        seen = {fc["test_id"] for r in fake.seen_reflections
                for fc in r["failing_cases"]}
        assert seen <= set(run.train_test_ids)
        assert not (seen & set(run.heldout_test_ids))
        # cost is bounded by the projection
        proj = project_runs(1, 1, has_heldout=True)
        assert run.n_agent_runs <= proj
        assert run.total_cost_usd > 0                       # accounted, not free
        # lineage: baseline (v0) + adopted (v1)
        assert [v.version for v in run.lineage] == [0, 1]
        assert run.lineage[1].parent_version == 0
        # persists + round-trips
        got = reg.get_optimization_run(run.run_id)
        assert got["status"] == "succeeded"
        assert got["run"]["best_prompt"] == "GOOD PROMPT"
        art = reg.get_optimization_artifact(run.run_id)
        assert art.best_version == 1
        assert any(s["run_id"] == run.run_id
                   for s in reg.list_optimization_runs())

    def test_rejects_regressor_keeps_baseline(self, reg, monkeypatch):
        suite_id = _seed_suite(reg, n=6)

        def behavior_for(prompt, suite_id_, tid):
            if "BAD" in prompt:
                # passes more cases but destroys 'tone' on every case
                return True, {"acc": 1.0, "tone": 0.0}
            fail = tid in ("tc-0", "tc-1", "tc-2")
            return (not fail), {"acc": 0.0 if fail else 1.0, "tone": 1.0}
        _patch_score(monkeypatch, behavior_for)
        fake = FakeOptimizer([{"prompt": "BAD PROMPT", "rationale": "regress"}])

        run = asyncio.run(optmod.optimize(
            CFG, reg, "agent-under-test", suite_id, rounds=1,
            candidates_per_round=1, heldout_fraction=0.34, seed=7,
            baseline_prompt="BASE", optimizer=fake, max_agent_runs=60))

        assert not run.improved
        assert run.best_version == 0                        # baseline kept
        assert run.best_prompt == "BASE"
        cand = run.rounds[0].candidates[0]
        assert not cand.accepted
        assert any(r.criterion_id == "tone" for r in cand.regressions)
        # best heldout unchanged from baseline heldout (no new prompt scored)
        assert run.best_heldout_rate == run.baseline_heldout_rate

    def test_run_cap_bounds_cost(self, reg, monkeypatch):
        suite_id = _seed_suite(reg, n=6)

        def behavior_for(prompt, suite_id_, tid):
            return (tid in ("tc-0",)), {"acc": 1.0 if tid == "tc-0" else 0.0}
        _patch_score(monkeypatch, behavior_for)
        # would propose 3/round over 3 rounds, but the cap stops it early
        fake = FakeOptimizer([{"prompt": f"P{i}", "rationale": ""} for i in range(3)])

        run = asyncio.run(optmod.optimize(
            CFG, reg, "agent-under-test", suite_id, rounds=3,
            candidates_per_round=3, heldout_fraction=0.34, seed=7,
            baseline_prompt="BASE", optimizer=fake, max_agent_runs=5))

        assert run.n_agent_runs <= 5                        # hard cap respected
        assert run.status == "succeeded"
