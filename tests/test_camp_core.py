"""Guardrail-critical unit tests for the folded-in AgentCamp engine: Wilson
lower bound, the two-condition promotion gate, deterministic grading, and the
distillation export. Ported from AgentCamp's test_core.py against
``agenttic.camp``."""

import random

from agenttic.camp.gate import PromotionGate
from agenttic.camp.task import Case
from agenttic.camp.tasks import SupportTriageTask
from agenttic.camp.trace import Episode, distillation_records
from agenttic.camp.trainer import CampReport, wilson_lower_bound


def _report(passes, n, threshold=0.99, min_ep=200):
    return CampReport(
        task_id="t", agent_id="a", episodes=n, passes=passes,
        threshold=threshold, min_episodes_for_gate=min_ep,
    )


def test_wilson_bounds_are_ordered_and_in_range():
    assert wilson_lower_bound(0, 0) == 0.0
    lb_small = wilson_lower_bound(99, 100)
    lb_big = wilson_lower_bound(990, 1000)
    # Same 99% point estimate, but more data => a tighter (higher) lower bound.
    assert 0.0 <= lb_small < lb_big <= 1.0


def test_hard_floor_cannot_be_overridden_by_human():
    r = _report(passes=980, n=1000, threshold=0.99)  # 98% < 99% floor
    always_yes = PromotionGate(human_approver=lambda _r: True)
    decision = always_yes.evaluate(r)
    assert decision.promoted is False
    assert any("floor" in reason for reason in decision.reasons)


def test_insufficient_data_blocks_even_if_rate_is_high():
    r = _report(passes=50, n=50, threshold=0.80, min_ep=200)  # 100% but tiny n
    gate = PromotionGate(human_approver=lambda _r: True)
    decision = gate.evaluate(r)
    assert decision.promoted is False
    assert any("insufficient data" in reason for reason in decision.reasons)


def test_human_approval_required_even_when_floor_met():
    r = _report(passes=1000, n=1000, threshold=0.80)  # clears floor comfortably
    default_deny = PromotionGate()  # no approver
    assert default_deny.evaluate(r).promoted is False
    with_human = PromotionGate(human_approver=lambda _r: True)
    assert with_human.evaluate(r).promoted is True


def test_grader_requires_both_category_and_action():
    task = SupportTriageTask()
    case = Case(case_id="c", inputs={"message": "x"},
                gold={"category": "account", "priority": "urgent",
                      "action": "escalate_to_security"})
    right = task.grade(case, {"category": "account", "priority": "urgent",
                              "action": "escalate_to_security"})
    wrong_action = task.grade(case, {"category": "account", "priority": "urgent",
                                     "action": "reset_password"})
    assert right.passed is True and right.score == 1.0
    assert wrong_action.passed is False  # category right, action wrong => fail


def test_sampler_is_deterministic_under_seed():
    task = SupportTriageTask()
    a = [task.sample_case(random.Random(7)).gold for _ in range(1)]
    b = [task.sample_case(random.Random(7)).gold for _ in range(1)]
    assert a == b


def test_distillation_exports_only_passing_as_chat_records():
    eps = [
        Episode(episode_id="1", task_id="t", agent_id="a", timestamp=0.0,
                inputs={"message": "hi"}, action={"action": "answer_faq"},
                passed=True, score=1.0, system_prompt="sys"),
        Episode(episode_id="2", task_id="t", agent_id="a", timestamp=0.0,
                inputs={"message": "bad"}, action={"action": "wrong"},
                passed=False, score=0.0, system_prompt="sys"),
    ]
    records = list(distillation_records(iter(eps), only_passing=True))
    assert len(records) == 1  # the failing episode is excluded
    msgs = records[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
    assert msgs[1]["content"] == "hi"
    assert records[0]["meta"]["task_id"] == "t"
