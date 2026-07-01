"""Tests for the self-improvement loop: the frozen holdout anchor, the
challenger/champion ratchet, and the collapse guard. Ported from AgentCamp's
test_loop.py against ``ascore.camp``."""

import os

from ascore.camp.holdout import FrozenHoldout
from ascore.camp.improve import (
    ImprovementLoop,
    LoopConfig,
    RuleSupportAgent,
    degenerate_factory,
    honest_factory,
)
from ascore.camp.tasks import SupportTriageTask
from ascore.camp.trace import MemoryTraceStore, TraceStore


def _holdout(n=600, seed=123):
    return FrozenHoldout(SupportTriageTask(), n=n, seed=seed)


def _store():
    return MemoryTraceStore()


def test_frozen_holdout_is_stable_and_deterministic():
    h = _holdout()
    fp1 = h.fingerprint
    r1 = h.evaluate(RuleSupportAgent(generation=0)).passes
    r2 = h.evaluate(RuleSupportAgent(generation=0)).passes
    assert h.fingerprint == fp1        # set never changes
    assert r1 == r2                    # evaluation is deterministic


def test_honest_loop_improves_over_baseline():
    task = SupportTriageTask()
    h = _holdout()
    baseline = h.evaluate(RuleSupportAgent(generation=0)).pass_rate
    loop = ImprovementLoop(task, h, _store(), factory=honest_factory)
    result = loop.run(LoopConfig(rounds=5, episodes_per_round=400,
                                 accuracy_floor=0.95, seed=1),
                      human_approver=lambda _r: True)
    assert result.final_holdout_rate > baseline + 0.05   # meaningful gain
    assert result.final_champion_gen >= 1                 # a challenger was promoted


def test_ratchet_never_regresses_and_degenerate_does_not_promote():
    task = SupportTriageTask()
    h = _holdout()
    baseline = h.evaluate(RuleSupportAgent(generation=0)).pass_rate
    loop = ImprovementLoop(task, h, _store(), factory=degenerate_factory)
    result = loop.run(LoopConfig(rounds=6, episodes_per_round=400,
                                 accuracy_floor=0.99, seed=1),
                      human_approver=lambda _r: True)
    # No ground truth -> no promotion, and the champion must not fall below baseline.
    assert result.promoted is False
    assert result.final_holdout_rate >= baseline
    assert "stall" in result.halted_reason or "escalate" in result.halted_reason


def test_floor_still_blocks_promotion_even_with_human():
    task = SupportTriageTask()
    h = _holdout()
    loop = ImprovementLoop(task, h, _store(), factory=honest_factory)
    # Honest loop reaches ~0.97; a 0.99 floor should still block promotion.
    result = loop.run(LoopConfig(rounds=6, episodes_per_round=400,
                                 accuracy_floor=0.99, seed=1),
                      human_approver=lambda _r: True)
    assert result.promoted is False


def test_trace_store_roundtrips_and_exports(tmp_path):
    # The file-backed TraceStore still works (used by the CLI-style path).
    path = os.path.join(tmp_path, "memory.jsonl")
    store = TraceStore(path)
    task = SupportTriageTask()
    from ascore.camp.agent import HeuristicSupportAgent
    from ascore.camp.environment import MockSupportEnv
    from ascore.camp.trainer import CampConfig, TrainingCamp
    import random
    camp = TrainingCamp(task, MockSupportEnv(task, random.Random(0)),
                        HeuristicSupportAgent(), store)
    report = camp.run(CampConfig(episodes=50, seed=0))
    assert len(store) == 50
    out = os.path.join(tmp_path, "distill.jsonl")
    n = store.export_distillation_jsonl(out, only_passing=True)
    assert n == report.passes
