"""Learning optimizer (SPEC-2 Step 14) — the springboard, with the LLM proposer
and the agent client mocked (no network, no spend):

- ONE optimization round PROMOTES a candidate that fixes a seeded failure and
  REJECTS a candidate that regresses another criterion (the gate reuses the
  Step-10 regression protection + adds ε / cost / latency budgets);
- rejected candidates persist in the config ledger with their reasons and are
  queryable back;
- the lineage query reconstructs the baseline→promoted chain with score deltas;
- a high-severity domain requires an explicit `learn approve` before the
  promotion goes live (it lands pending_approval until approved);
- the preference export produces valid JSONL pairs that parse back.

The agent/judge are never called: ``_run_candidate`` is stubbed to return a
deterministic scorecard chosen by the system prompt, and a fake proposer yields
fixed candidates (mirrors tests/test_optimizer.py + tests/test_scan.py style).
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest

from agenttic.learning import optimizer as learn
from agenttic.learning.optimizer import (
    collect_failures,
    config_hash_for,
    export_preferences,
    gate,
    run_learning,
    write_preferences_jsonl,
)
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.feedback import HumanFeedback
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import Span, Trace

CFG = {
    "models": {"agent_default": "agent-model", "judge_strong": "judge-model",
               "judge_light": "judge-light"},
    "harness": {"timeout_seconds": 10, "max_parallel": 5,
                "transport_retries": 1, "max_steps": 10},
    "scoring": {"calibration_threshold": 0.8},
    "paths": {"review_dir": "review/"},
    "learning": {"epsilon": 0.02, "max_cost_multiplier": 2.0,
                 "max_latency_multiplier": 2.0, "high_severity_domains": []},
    "budget": {},
    "security": {},
}

AGENT = "agent-under-test"


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "learn.db")


def _seed_suite(reg: Registry, suite_id="s-learn", n: int = 6) -> str:
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
    cases = [TestCase(test_id=f"tc-{i}", suite_id=suite_id, version=1,
                      task_description=f"task {i}", input={"x": i}, rubric_id="r")
             for i in range(n)]
    reg.save_suite(TestSuite(suite_id=suite_id, version=1, business_context="x",
                             test_ids=[c.test_id for c in cases], approved=True),
                   cases)
    return suite_id


def _card(reg, suite_id, behavior, agent_id=AGENT,
          cost=0.01, latency=100.0) -> Scorecard:
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
                             passed=passed, cost_usd=cost, scoring_cost_usd=0.002,
                             latency_ms=latency))
    sc = Scorecard.aggregate(
        scorecard_id=uuid.uuid4().hex[:12], agent_id=agent_id, suite_id=suite_id,
        suite_version=1, rubric_id="r", rubric_version=1, run_scores=runs,
        visibility_tier="glass_box")
    reg.save_scorecard(sc)
    return sc


class FakeProposer:
    """Yields fixed candidate prompts; records zero cost (mirrors test_optimizer's
    FakeOptimizer)."""
    def __init__(self, candidates):
        self._candidates = candidates
        self.last_cost_usd = 0.0
        self.seen = []

    def propose(self, current_prompt, reflection, n):
        self.seen.append(reflection)
        return self._candidates[:n]


def _patch_run(monkeypatch, behavior_for):
    """Stub _run_candidate: deterministic scorecard chosen by (system_prompt,
    suite_id, test_id). Screen sub-suites reuse the same behavior."""
    async def fake(cfg, reg, agent_id, system_prompt, suite_id, *, model="",
                   version=None, client=None, judge_client=None, on_progress=None):
        return _card(reg, suite_id,
                     lambda tid: behavior_for(system_prompt, tid),
                     agent_id=agent_id,
                     cost=behavior_for.__dict__.get("cost", 0.01),
                     latency=behavior_for.__dict__.get("latency", 100.0))
    monkeypatch.setattr(learn, "_run_candidate", fake)


# -- gate (regression + budgets) ---------------------------------------------

class TestGate:
    def _sc(self, scid, passes, crit, cost=0.01, latency=100.0):
        runs = [RunScore(trace_id=f"tr-{scid}-{t}", test_id=t,
                         criterion_scores=[CriterionScore(criterion_id=c, score=v,
                                                          scorer="code")
                                           for c, v in crit[t].items()],
                         passed=passes[t], cost_usd=cost, latency_ms=latency)
                for t in sorted(passes)]
        return Scorecard.aggregate(scorecard_id=scid, agent_id="a", suite_id="s",
                                   suite_version=1, rubric_id="r", rubric_version=1,
                                   run_scores=runs, visibility_tier="glass_box")

    def test_promotes_clean_improvement(self):
        ids = [f"t{i}" for i in range(4)]
        base = self._sc("b", {t: False for t in ids}, {t: {"acc": 0.0} for t in ids})
        cand = self._sc("c", {t: True for t in ids}, {t: {"acc": 1.0} for t in ids})
        ok, reason = gate(cand, base, CFG)
        assert ok and "accepted" in reason

    def test_rejects_regression(self):
        ids = [f"t{i}" for i in range(4)]
        base = self._sc("b", {t: (t in ("t0", "t1")) for t in ids},
                        {t: {"acc": 0.0, "tone": 1.0} for t in ids})
        cand = self._sc("c", {t: True for t in ids},
                        {t: {"acc": 1.0, "tone": 0.0} for t in ids})
        ok, reason = gate(cand, base, CFG)
        assert not ok and "regress" in reason

    def test_rejects_cost_blowout(self):
        ids = [f"t{i}" for i in range(4)]
        base = self._sc("b", {t: False for t in ids},
                        {t: {"acc": 0.0} for t in ids}, cost=0.01)
        cand = self._sc("c", {t: True for t in ids},
                        {t: {"acc": 1.0} for t in ids}, cost=0.05)  # 5x
        ok, reason = gate(cand, base, CFG)
        assert not ok and "cost" in reason

    def test_rejects_latency_blowout(self):
        ids = [f"t{i}" for i in range(4)]
        base = self._sc("b", {t: False for t in ids},
                        {t: {"acc": 0.0} for t in ids}, latency=100.0)
        cand = self._sc("c", {t: True for t in ids},
                        {t: {"acc": 1.0} for t in ids}, latency=500.0)  # 5x
        ok, reason = gate(cand, base, CFG)
        assert not ok and "latency" in reason


# -- collect_failures (the dossier, feedback folded in) ----------------------

class TestDossier:
    def test_folds_judge_and_human_signal(self, reg):
        suite_id = _seed_suite(reg, n=4)

        def behavior(tid):
            if tid in ("tc-0", "tc-1"):
                return False, {"acc": 0.0, "tone": 1.0}   # fail acc
            return True, {"acc": 1.0, "tone": 1.0}
        sc = _card(reg, suite_id, behavior)
        fb = [HumanFeedback(
                feedback_id="f1", trace_id="tr-x", agent_id=AGENT,
                source="reviewer", kind="correction",
                corrected_output="the correct answer is 42",
                rationale="agent hallucinated", created_at=datetime.now(timezone.utc)),
              HumanFeedback(
                feedback_id="f2", trace_id="tr-y", agent_id=AGENT,
                source="end_user", kind="rating", criterion_id="tone",
                rating=0.0, rationale="too curt",
                created_at=datetime.now(timezone.utc))]
        dossier = collect_failures(sc, [], fb)
        assert dossier["n_failing"] == 2
        assert "acc" in dossier["failing_criteria"]
        assert dossier["n_human_corrections"] == 1
        assert dossier["human_corrections"][0]["corrected_output"].startswith("the correct")
        assert any(c["criterion_id"] == "tone"
                   for c in dossier["human_flagged_criteria"])


# -- the loop: promote vs reject ---------------------------------------------

class TestLearnLoop:
    def test_promotes_fixer_and_rejects_regressor(self, reg, monkeypatch):
        suite_id = _seed_suite(reg, n=6)

        def behavior_for(prompt, tid):
            if "GOOD" in prompt:                 # fixer: fixes acc, keeps tone
                return True, {"acc": 1.0, "tone": 1.0}
            if "BAD" in prompt:                  # passes more but tanks tone
                return True, {"acc": 1.0, "tone": 0.0}
            fail = tid in ("tc-0", "tc-1", "tc-2")   # baseline: half fail acc
            return (not fail), {"acc": 0.0 if fail else 1.0, "tone": 1.0}
        _patch_run(monkeypatch, behavior_for)
        proposer = FakeProposer([
            {"prompt": "GOOD PROMPT", "rationale": "fix acc"},
            {"prompt": "BAD PROMPT", "rationale": "regress tone"}])

        summary = asyncio.run(run_learning(
            reg, CFG, AGENT, suite_id, rounds=1, candidates_per_round=2,
            baseline_prompt="BASE", proposer=proposer, feedback=[]))

        # exactly one promotion (the GOOD candidate), no pending gate
        assert len(summary["promoted"]) == 1
        good_hash = config_hash_for(AGENT, "GOOD PROMPT")
        bad_hash = config_hash_for(AGENT, "BAD PROMPT")
        assert summary["promoted"][0].agent_config_hash == good_hash
        assert summary["promoted"][0].status == "promoted"
        assert summary["promoted"][0].parent_hash == summary["baseline_hash"]
        # the regressor is recorded as a rejection with a reason
        rejected_hashes = {ac.agent_config_hash for ac in summary["rejected"]}
        assert bad_hash in rejected_hashes

    def test_rejected_candidates_persist_with_reasons(self, reg, monkeypatch):
        suite_id = _seed_suite(reg, n=6)

        def behavior_for(prompt, tid):
            if "BAD" in prompt:
                return True, {"acc": 1.0, "tone": 0.0}
            fail = tid in ("tc-0", "tc-1", "tc-2")
            return (not fail), {"acc": 0.0 if fail else 1.0, "tone": 1.0}
        _patch_run(monkeypatch, behavior_for)
        proposer = FakeProposer([{"prompt": "BAD PROMPT", "rationale": "regress"}])

        summary = asyncio.run(run_learning(
            reg, CFG, AGENT, suite_id, rounds=1, candidates_per_round=1,
            baseline_prompt="BASE", proposer=proposer, feedback=[]))
        assert not summary["promoted"]

        # query the ledger back — the rejection is stored with its reason
        bad_hash = config_hash_for(AGENT, "BAD PROMPT")
        stored = reg.get_agent_config(bad_hash)
        assert stored.status == "rejected"
        assert stored.reason and "regress" in stored.reason
        # it appears in the agent's lineage too
        chain = reg.agent_config_lineage(AGENT)
        assert bad_hash in {ac.agent_config_hash for ac in chain}

    def test_lineage_reconstructs_baseline_to_promoted_with_deltas(
            self, reg, monkeypatch):
        suite_id = _seed_suite(reg, n=6)

        def behavior_for(prompt, tid):
            if "GOOD" in prompt:
                return True, {"acc": 1.0, "tone": 1.0}
            fail = tid in ("tc-0", "tc-1", "tc-2")
            return (not fail), {"acc": 0.0 if fail else 1.0, "tone": 1.0}
        _patch_run(monkeypatch, behavior_for)
        proposer = FakeProposer([{"prompt": "GOOD PROMPT", "rationale": "fix acc"}])

        summary = asyncio.run(run_learning(
            reg, CFG, AGENT, suite_id, rounds=1, candidates_per_round=1,
            baseline_prompt="BASE", proposer=proposer, feedback=[]))

        chain = reg.agent_config_lineage(AGENT)
        # baseline first, promoted child after, chained by parent_hash
        assert chain[0].parent_hash == ""
        assert chain[0].agent_config_hash == summary["baseline_hash"]
        promoted = summary["promoted"][0]
        child = next(ac for ac in chain
                     if ac.agent_config_hash == promoted.agent_config_hash)
        assert child.parent_hash == chain[0].agent_config_hash
        # score delta baseline -> promoted is positive (fixer improved acc)
        base_rate = reg.get_scorecard(
            chain[0].scorecard_ids[0]).task_success_rate
        child_rate = reg.get_scorecard(
            child.scorecard_ids[0]).task_success_rate
        assert child_rate > base_rate

    def test_high_severity_domain_requires_approval(self, reg, monkeypatch):
        # suite id carries the high-severity 'refunds' domain token
        suite_id = _seed_suite(reg, suite_id="refunds-suite", n=6)
        cfg = {**CFG, "learning": {**CFG["learning"],
                                   "high_severity_domains": ["refunds"]}}

        def behavior_for(prompt, tid):
            if "GOOD" in prompt:
                return True, {"acc": 1.0, "tone": 1.0}
            fail = tid in ("tc-0", "tc-1", "tc-2")
            return (not fail), {"acc": 0.0 if fail else 1.0, "tone": 1.0}
        _patch_run(monkeypatch, behavior_for)
        proposer = FakeProposer([{"prompt": "GOOD PROMPT", "rationale": "fix"}])

        summary = asyncio.run(run_learning(
            reg, cfg, AGENT, suite_id, rounds=1, candidates_per_round=1,
            baseline_prompt="BASE", proposer=proposer, feedback=[]))

        # NOT auto-promoted — it is pending until a human approves
        assert not summary["promoted"]
        assert len(summary["pending"]) == 1
        pend = summary["pending"][0]
        assert pend.status == "pending_approval"
        assert pend in reg.pending_agent_configs(AGENT) or \
            pend.agent_config_hash in {
                p.agent_config_hash for p in reg.pending_agent_configs(AGENT)}

        # approve -> promoted, and the pending queue drains
        approved = reg.mark_agent_config_approved(pend.agent_config_hash, "alice")
        assert approved.status == "promoted"
        assert approved.approved_by == "alice"
        assert not reg.pending_agent_configs(AGENT)


# -- preference export --------------------------------------------------------

class TestPreferenceExport:
    def test_export_produces_valid_jsonl_pairs(self, reg, tmp_path, monkeypatch):
        suite_id = _seed_suite(reg, n=4)
        # persist traces so the export can join trace bodies to scores
        for i, (tid, passed) in enumerate(
                [("tc-0", True), ("tc-1", False), ("tc-2", True), ("tc-3", False)]):
            tr = Trace(
                trace_id=f"tr-{tid}", agent_id=AGENT, agent_config_hash="h",
                test_case_id=tid, visibility="glass_box",
                final_output=f"output for {tid} (passed={passed})",
                spans=[Span(span_id="s0", kind="llm_call", name="call",
                            start_time=datetime.now(timezone.utc),
                            end_time=datetime.now(timezone.utc),
                            input={}, output={})])
            reg.save_trace(tr)

        def behavior(tid):
            passed = tid in ("tc-0", "tc-2")
            return passed, {"acc": 1.0 if passed else 0.0}
        # build a scorecard whose run trace_ids match the persisted traces
        _suite, cases = reg.get_suite(suite_id)
        runs = []
        for c in cases:
            passed, crit = behavior(c.test_id)
            runs.append(RunScore(
                trace_id=f"tr-{c.test_id}", test_id=c.test_id,
                criterion_scores=[CriterionScore(criterion_id="acc",
                                                 score=1.0 if passed else 0.0,
                                                 scorer="judge")],
                passed=passed, cost_usd=0.01))
        # give tc-0 and tc-1 the SAME test case grouping by reusing test ids is
        # per-case; to get a within-case pair we add a second scorecard where the
        # same case scores differently.
        sc1 = Scorecard.aggregate(
            scorecard_id="sc1", agent_id=AGENT, suite_id=suite_id, suite_version=1,
            rubric_id="r", rubric_version=1, run_scores=runs,
            visibility_tier="glass_box")
        reg.save_scorecard(sc1)
        # a second run of tc-1 that PASSED (different trace) -> within-case pair
        tr2 = Trace(trace_id="tr-tc-1b", agent_id=AGENT, agent_config_hash="h",
                    test_case_id="tc-1", visibility="glass_box",
                    final_output="better output for tc-1",
                    spans=[Span(span_id="s0", kind="llm_call", name="c",
                                start_time=datetime.now(timezone.utc),
                                end_time=datetime.now(timezone.utc),
                                input={}, output={})])
        reg.save_trace(tr2)
        sc2 = Scorecard.aggregate(
            scorecard_id="sc2", agent_id=AGENT, suite_id=suite_id, suite_version=1,
            rubric_id="r", rubric_version=1,
            run_scores=[RunScore(
                trace_id="tr-tc-1b", test_id="tc-1",
                criterion_scores=[CriterionScore(criterion_id="acc", score=1.0,
                                                 scorer="judge")],
                passed=True, cost_usd=0.01)],
            visibility_tier="glass_box")
        reg.save_scorecard(sc2)

        pairs = export_preferences(reg, AGENT)
        assert pairs, "expected at least one preference pair"
        # a within-case pair for tc-1 (passed beats failed)
        tc1 = [p for p in pairs if p["test_id"] == "tc-1"]
        assert tc1 and tc1[0]["chosen"]["score"] > tc1[0]["rejected"]["score"]

        out = tmp_path / "prefs.jsonl"
        n = write_preferences_jsonl(pairs, out)
        assert n == len(pairs)
        # parse the JSONL back — every line is a valid pair
        lines = out.read_text().splitlines()
        assert len(lines) == n
        for line in lines:
            obj = json.loads(line)
            assert obj["chosen"]["score"] > obj["rejected"]["score"]
            assert "test_id" in obj and "margin" in obj
