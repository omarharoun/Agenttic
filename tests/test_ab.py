"""A/B run + comparison, end-to-end with mocked LLMs on the pilot suite:
- a run produces both scorecards and a comparison;
- paired cases line up by test_id;
- a genuine behavioral difference is detected as a significant win;
- two identical-behavior variants come back as no significant difference;
- cases that error in scoring are excluded from the pairing (consistent with
  the errored-vs-failed scorecard rule).
"""

import asyncio
import json
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ascore.ab import compare_scorecards, effective_agent_ids, run_ab_op
from ascore.registry.sqlite_store import Registry
from ascore.schema.ab import ABVariant
from ascore.schema.rubric import Rubric
from ascore.schema.scorecard import CriterionScore, RunScore, Scorecard
from ascore.schema.testcase import TestCase, TestSuite
from tests.test_e2e_pipeline import ProfessionalToneJudgeClient

PILOT = Path(__file__).parent.parent / "examples" / "pilot_support_triage"

CFG = {
    "models": {"agent_default": "agent-model", "judge_strong": "judge-model",
               "judge_light": "judge-light"},
    "harness": {"timeout_seconds": 10, "max_parallel": 5,
                "transport_retries": 1, "max_steps": 10},
    "scoring": {"calibration_threshold": 0.8},
    "paths": {"review_dir": "review/"},
    "live": {"drift_threshold": 0.15},
    "budget": {},
    "security": {},
}


class RoutingClient:
    """Mocked routing agent like the e2e one, but the misroute behavior is a
    constructor flag so two variants can differ: ``perfect=False`` deliberately
    misroutes billing tickets to 'general' (failing those cases)."""

    def __init__(self, perfect: bool = True):
        self.perfect = perfect
        self.messages = NS(create=self._create)
        self._lock = threading.Lock()

    def _create(self, **kw):
        with self._lock:
            msgs = kw["messages"]
            ticket = json.loads(msgs[0]["content"])["ticket"]
            has_tool_result = any(
                isinstance(m.get("content"), list)
                and any(isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in m["content"])
                for m in msgs)
            if not has_tool_result:
                return NS(stop_reason="tool_use",
                          usage=NS(input_tokens=200, output_tokens=30),
                          content=[NS(type="tool_use", name="lookup_kb",
                                      input={"key": "routing_rules"},
                                      id=f"tu_{uuid.uuid4().hex[:6]}")])
            t = ticket.lower()
            if any(w in t for w in ("refund", "charge", "invoice")):
                queue = "billing" if self.perfect else "general"  # B-only failures
            elif any(w in t for w in ("crash", "error", "bug", "password")):
                queue = "technical"
            else:
                queue = "general"
            return NS(stop_reason="end_turn",
                      usage=NS(input_tokens=260, output_tokens=8),
                      content=[NS(type="text", text=queue)])


def _load_pilot(reg: Registry) -> str:
    reg.save_rubric(Rubric.model_validate_json((PILOT / "rubric.json").read_text()))
    suite = TestSuite.model_validate_json((PILOT / "suite.json").read_text())
    cases = [TestCase.model_validate(c)
             for c in json.loads((PILOT / "cases.json").read_text())]
    reg.save_suite(suite, cases)
    reg.approve_suite(suite.suite_id, suite.version)
    return suite.suite_id


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "ab.db")


class TestRunAB:
    def test_produces_two_scorecards_and_comparison(self, reg):
        suite_id = _load_pilot(reg)
        va = ABVariant(label="A", agent_id="router", model="m1")
        vb = ABVariant(label="B", agent_id="router", model="m2")
        judge = ProfessionalToneJudgeClient()
        # A perfect, B misroutes billing -> A should beat B on routing
        ca = {"agent": RoutingClient(perfect=True), "judge": judge}
        cb = {"agent": RoutingClient(perfect=False), "judge": judge}

        comp = asyncio.run(run_ab_op(
            CFG, reg, suite_id, va, vb, clients_a=ca, clients_b=cb))

        # both scorecards persisted and addressable
        assert reg.get_scorecard(comp.scorecard_a_id).agent_id == "router::A"
        assert reg.get_scorecard(comp.scorecard_b_id).agent_id == "router::B"
        # comparison persisted and retrievable
        again = reg.get_ab_comparison(comp.comparison_id)
        assert again.comparison_id == comp.comparison_id
        # paired over the same cases; A (perfect) wins
        assert comp.n_paired > 0
        assert comp.success_rate_a >= comp.success_rate_b
        assert comp.winner in ("A", "tie")
        assert comp.flipped_cases  # at least one case flipped between variants
        # flipped billing cases are "loss" (A pass -> B fail)
        assert any(f.direction == "loss" for f in comp.flipped_cases)

    def test_identical_variants_no_difference(self, reg):
        suite_id = _load_pilot(reg)
        va = ABVariant(label="A", agent_id="x")
        vb = ABVariant(label="B", agent_id="y")
        judge = ProfessionalToneJudgeClient()
        ca = {"agent": RoutingClient(perfect=True), "judge": judge}
        cb = {"agent": RoutingClient(perfect=True), "judge": judge}
        comp = asyncio.run(run_ab_op(
            CFG, reg, suite_id, va, vb, clients_a=ca, clients_b=cb))
        assert comp.success_rate_a == comp.success_rate_b
        assert comp.winner == "tie"
        assert "No significant difference" in comp.verdict
        assert not comp.flipped_cases  # identical behavior -> no flips

    def test_effective_ids_disambiguate_shared_agent(self):
        a, b = effective_agent_ids(
            ABVariant(label="A", agent_id="same"),
            ABVariant(label="B", agent_id="same"))
        assert a == "same::A" and b == "same::B"
        a, b = effective_agent_ids(
            ABVariant(label="A", agent_id="one"),
            ABVariant(label="B", agent_id="two"))
        assert a == "one" and b == "two"


# -- comparison math on hand-built scorecards (no run needed) ----------------

def _sc(scid, agent, passes, crit_scores, errored=()):
    """Build a scorecard: ``passes`` maps test_id->bool; ``crit_scores`` maps
    test_id->{criterion: score}; ``errored`` test_ids get a scoring_error."""
    runs = []
    for tid in sorted(set(passes) | set(errored)):
        if tid in errored:
            runs.append(RunScore(trace_id=f"tr-{agent}-{tid}", test_id=tid,
                                 criterion_scores=[], passed=False,
                                 cost_usd=0.01, latency_ms=100, steps=2,
                                 scoring_error="ScoringError: boom"))
            continue
        cs = [CriterionScore(criterion_id=c, score=v, scorer="code")
              for c, v in crit_scores.get(tid, {}).items()]
        runs.append(RunScore(trace_id=f"tr-{agent}-{tid}", test_id=tid,
                             criterion_scores=cs, passed=passes[tid],
                             cost_usd=0.01, latency_ms=100, steps=2))
    return Scorecard.aggregate(
        scorecard_id=scid, agent_id=agent, suite_id="s", suite_version=1,
        rubric_id="r", rubric_version=1, run_scores=runs,
        visibility_tier="glass_box")


class TestCompareMath:
    def test_clear_win_significant(self):
        ids = [f"tc-{i}" for i in range(20)]
        a = _sc("a", "A", {t: False for t in ids}, {t: {"acc": 0.0} for t in ids})
        b = _sc("b", "B", {t: True for t in ids}, {t: {"acc": 1.0} for t in ids})
        comp = compare_scorecards("cmp1", a, b,
                                  ABVariant(label="A", agent_id="A"),
                                  ABVariant(label="B", agent_id="B"))
        assert comp.n_paired == 20
        assert comp.mcnemar["significant"]
        assert comp.winner == "B"
        assert "beats" in comp.verdict and "significant" in comp.verdict
        acc = next(c for c in comp.per_criterion if c.criterion_id == "acc")
        assert acc.significant and acc.direction == "B"
        assert all(f.direction == "gain" for f in comp.flipped_cases)

    def test_errored_cases_excluded_from_pairing(self):
        ids = [f"tc-{i}" for i in range(6)]
        # A scores all; B errors on tc-0 and tc-1
        a = _sc("a", "A", {t: True for t in ids}, {t: {"acc": 1.0} for t in ids})
        b = _sc("b", "B", {t: True for t in ids if t not in ("tc-0", "tc-1")},
                {t: {"acc": 1.0} for t in ids}, errored=("tc-0", "tc-1"))
        comp = compare_scorecards("cmp2", a, b,
                                  ABVariant(label="A", agent_id="A"),
                                  ABVariant(label="B", agent_id="B"))
        assert comp.n_paired == 4                       # 6 - 2 errored
        assert set(comp.excluded_test_ids) == {"tc-0", "tc-1"}

    def test_all_errored_one_side_no_pairs(self):
        ids = [f"tc-{i}" for i in range(3)]
        a = _sc("a", "A", {t: True for t in ids}, {t: {"acc": 1.0} for t in ids})
        b = _sc("b", "B", {}, {}, errored=tuple(ids))
        comp = compare_scorecards("cmp3", a, b,
                                  ABVariant(label="A", agent_id="A"),
                                  ABVariant(label="B", agent_id="B"))
        assert comp.n_paired == 0
        assert comp.winner == "tie"
        assert "No paired cases" in comp.verdict
