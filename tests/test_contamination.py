"""SPEC-6 Step 28 — contamination stance: canary + perturbation probes."""

from __future__ import annotations

import uuid

import pytest

from agenttic.integrity.contamination import contamination_check, make_canary
from agenttic.ops import report_op
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import SCHEMA_VERSION, Trace


def _trace(agent_id, out, tid):
    return Trace(trace_id=uuid.uuid4().hex, agent_id=agent_id, agent_config_hash="h",
                 test_case_id=tid, spans=[], visibility="black_box", final_output=out,
                 schema_version=SCHEMA_VERSION)


class ParrotAgent:
    """Contaminated: memorised the full canary from training and completes it
    when shown the prefix (regurgitation)."""
    agent_id = "parrot"

    def __init__(self, canary):
        self.canary = canary

    def run(self, test_input, *, test_case_id=None):
        hint = str(test_input.get("instruction", ""))
        out = self.canary if self.canary[:20] in hint else ""
        return _trace(self.agent_id, out, test_case_id)


class MemorizerAgent:
    """Contaminated: memorised (case -> answer); ignores the actual input."""
    agent_id = "memorizer"

    def __init__(self, memo):
        self.memo = memo

    def run(self, test_input, *, test_case_id=None):
        return _trace(self.agent_id, self.memo.get(test_case_id, ""), test_case_id)


class SkilledAgent:
    """Clean: reads the input and answers from it, so perturbation doesn't break it."""
    agent_id = "skilled"

    def run(self, test_input, *, test_case_id=None):
        return _trace(self.agent_id, str(test_input.get("answer", "")), test_case_id)


@pytest.fixture
def reg(tmp_path):
    r = Registry(tmp_path / "t.db")
    r.save_rubric(Rubric(rubric_id="rb", version=1, weights={"m": 1.0}, criteria=[
        Criterion(criterion_id="m", description="d", scorer="code", scale="binary",
                  check_ref="final_output_matches_expected")]))
    # each case: expected == the "answer" token in its input (skilled agent solves it)
    cases = [TestCase(test_id=f"c{i}", suite_id="s", task_description="Say the colour.",
                      input={"answer": colour}, expected={"final_output": colour},
                      rubric_id="rb")
             for i, colour in enumerate(["scarlet", "cobalt", "amber"])]
    r.save_suite(TestSuite(suite_id="s", version=1, business_context="pilot",
                           test_ids=[c.test_id for c in cases]), cases)
    return r


def test_canary_distinct_across_tenants_and_stable():
    a1 = make_canary("tenantA", "s", 1)
    a2 = make_canary("tenantA", "s", 1)
    b = make_canary("tenantB", "s", 1)
    assert a1 == a2                       # deterministic / stable
    assert a1 != b                        # distinct across tenants
    assert make_canary("tenantA", "s", 2) != a1   # distinct across versions


def test_parrot_agent_flagged_for_canary_regurgitation(reg):
    canary = reg.get_or_create_canary("s", 1)   # what a contaminated agent memorised
    rep = contamination_check(reg, ParrotAgent(canary), "s")
    assert rep.canary_regurgitated and rep.exposed


def test_memorizer_flagged_by_perturbation_gap(reg):
    # memorised the exact stored answers -> passes originals, fails perturbations
    memo = {"c0": "scarlet", "c1": "cobalt", "c2": "amber"}
    rep = contamination_check(reg, MemorizerAgent(memo), "s")
    assert not rep.canary_regurgitated
    assert rep.perturbation_gap == 1.0 and rep.exposed


def test_skilled_agent_not_flagged(reg):
    rep = contamination_check(reg, SkilledAgent(), "s")
    assert not rep.canary_regurgitated
    assert rep.perturbation_gap == 0.0 and not rep.exposed


def test_report_renders_contamination_line(reg):
    rep = contamination_check(reg, SkilledAgent(), "s")
    runs = [RunScore(trace_id="t", test_id="c0",
                     criterion_scores=[CriterionScore(criterion_id="m", score=1.0, scorer="code")],
                     passed=True)]
    sc = Scorecard.aggregate(scorecard_id="sc1", agent_id="skilled", suite_id="s",
                             suite_version=1, rubric_id="rb", rubric_version=1,
                             run_scores=runs, visibility_tier="black_box")
    reg.save_scorecard(sc)
    reg.save_contamination_report("sc1", rep)
    md = report_op(reg, "sc1")
    assert "## Contamination" in md
    assert "Suite origin: private" in md and "agent exposure: none detected" in md
