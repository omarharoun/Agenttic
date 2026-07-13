"""
Camp orchestration — the glue between the vendored engine and Agenttic.

Pure, side-effect-free with respect to the DB: these functions run a camp (or an
improve loop) in memory and return plain dicts + the episode list. The router
(:mod:`agenttic.server.routes.camp`) is what persists them via ``CampStore``. That
split keeps the trainer/gate/holdout code exactly as tested and makes the whole
thing easy to unit-test without a database or a network.

The promotion gate is preserved precisely: the hard accuracy floor is the Wilson
95% lower bound, checked inside ``PromotionGate.evaluate`` and **non-overridable**
— a human sign-off (an authenticated operator, threaded through as
``approved_by``) is the required *second* condition, never a substitute for the
floor.
"""

from __future__ import annotations

import random
from dataclasses import asdict
from typing import Any, Callable, Optional

from .adapter_agent import AdapterAgent
from .agent import Agent, HeuristicSupportAgent
from .environment import MockSupportEnv
from .gate import PromotionGate
from .holdout import FrozenHoldout
from .improve import (
    ImprovementLoop,
    LoopConfig,
    degenerate_factory,
    honest_factory,
)
from .task import Task
from .tasks import SupportTriageTask
from .trace import MemoryTraceStore
from .trainer import CampConfig, CampReport, TrainingCamp

# The task catalogue exposed as camp "environments". More tasks (each a case
# sampler + deterministic grader) plug in here without touching anything else.
TASKS: dict[str, type] = {
    "support_triage": SupportTriageTask,
}

MODES = ("mock", "agent")


def available_tasks() -> list[dict]:
    out = []
    for tid, cls in TASKS.items():
        inst = cls()
        out.append({"task_id": tid, "name": getattr(inst, "name", tid)})
    return out


def get_task(task_id: str) -> Task:
    if task_id not in TASKS:
        raise KeyError(task_id)
    return TASKS[task_id]()


def report_to_dict(report: CampReport) -> dict:
    return {
        "task_id": report.task_id,
        "agent_id": report.agent_id,
        "episodes": report.episodes,
        "passes": report.passes,
        "pass_rate": report.pass_rate,
        "wilson_lower_95": report.wilson_lower_95,
        "threshold": report.threshold,
        "min_episodes_for_gate": report.min_episodes_for_gate,
        "enough_data": report.enough_data,
        "meets_floor": report.meets_threshold(),
        "summary": report.summary(),
    }


def evaluate_gate(report: CampReport, approved_by: Optional[str] = None) -> dict:
    """Evaluate the two-condition promotion gate against a report.

    ``approved_by`` is the identity of the human operator who signed off (an
    email), or ``None`` for "no human present". The floor is checked inside the
    gate and cannot be waved through — the approver only matters once it's met.
    """
    approver = (lambda _r: True) if approved_by else None
    decision = PromotionGate(human_approver=approver).evaluate(report)
    return {
        "promoted": decision.promoted,
        "reasons": decision.reasons,
        "summary": decision.summary(),
        "enough_data": report.enough_data,
        "floor_met": report.meets_threshold(),
        "human_approved": bool(approved_by),
        "approved_by": approved_by,
        "threshold": report.threshold,
        "wilson_lower_95": report.wilson_lower_95,
    }


def _throttled(on_progress, total, phase_fn):
    """Wrap a raw progress hook so it fires at most ~50 times over the run —
    keeps the DB write rate sane for large episode counts."""
    if on_progress is None:
        return None
    step = max(1, total // 50)
    last = [0]

    def hook(done, _tot):
        if done == total or done - last[0] >= step:
            last[0] = done
            on_progress(done, total, phase_fn(done))
    return hook


def run_single_camp(
    *,
    task_id: str = "support_triage",
    mode: str = "mock",
    episodes: int = 500,
    threshold: float = 0.99,
    min_episodes_for_gate: int = 200,
    seed: int = 0,
    adapter: Any = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Run one camp session; return report + (pre-approval) gate + episodes.

    ``on_progress(done, total, phase)`` is called periodically as episodes
    complete (used by the async runner to persist live progress)."""
    task = get_task(task_id)
    rng = random.Random(seed)
    env = MockSupportEnv(task, rng)

    agent: Agent
    if mode == "agent":
        if adapter is None:
            raise ValueError("mode='agent' requires an adapter to run under camp")
        agent = AdapterAgent(adapter)
    else:
        agent = HeuristicSupportAgent()

    store = MemoryTraceStore()
    camp = TrainingCamp(task, env, agent, store)
    hook = _throttled(on_progress, episodes,
                      lambda done: f"{done}/{episodes} episodes")
    report = camp.run(CampConfig(
        episodes=episodes, accuracy_threshold=threshold,
        min_episodes_for_gate=min_episodes_for_gate, seed=seed),
        on_episode=hook)

    # Fresh run => no human has signed off yet: gate denies by default.
    gate = evaluate_gate(report, approved_by=None)
    return {
        "report": report_to_dict(report),
        "report_obj": report,
        "gate": gate,
        "episodes": store.episodes,
        "agent_label": agent.agent_id,
    }


def run_improve_camp(
    *,
    task_id: str = "support_triage",
    rounds: int = 5,
    episodes_per_round: int = 300,
    threshold: float = 0.99,
    holdout: int = 600,
    seed: int = 0,
    degenerate: bool = False,
    approved_by: Optional[str] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Run the self-improving champion/challenger loop with the frozen-holdout
    ratchet; return the final report, gate, per-round log, review queue and the
    full cross-round episode memory.

    ``on_progress(done, total, phase)`` reports coarse per-round progress (the
    loop is mock-only and fast, so round granularity is plenty)."""
    task = get_task(task_id)
    frozen = FrozenHoldout(task, n=holdout, seed=seed + 10_000)
    store = MemoryTraceStore()
    factory = degenerate_factory if degenerate else honest_factory

    loop = ImprovementLoop(task, frozen, store, factory=factory)
    approver = (lambda _r: True) if approved_by else None
    total_eps = rounds * episodes_per_round
    on_round = None
    if on_progress is not None:
        def on_round(r, total_rounds):  # noqa: E306 — local hook
            on_progress(min(r * episodes_per_round, total_eps), total_eps,
                        f"round {r}/{total_rounds}")
    result = loop.run(
        LoopConfig(rounds=rounds, episodes_per_round=episodes_per_round,
                   accuracy_floor=threshold, seed=seed),
        human_approver=approver, on_round=on_round)

    # Measure the final champion on the frozen anchor to build a gate-compatible
    # report (Wilson lower bound over the held-out set — never trained on).
    final = frozen.evaluate(loop.champion)
    report = CampReport(
        task_id=task.task_id, agent_id=loop.champion.agent_id,
        episodes=final.n, passes=final.passes, threshold=threshold,
        min_episodes_for_gate=min(200, final.n))
    gate = evaluate_gate(report, approved_by=approved_by)

    rounds_log = [asdict(r) for r in result.rounds]
    review_queue = [
        {
            "message": case.inputs.get("message"),
            "agent_action": action,
            "correct": case.gold,
            "why": grade.detail,
        }
        for case, action, grade in frozen.failing_cases(loop.champion)
    ]

    report_dict = report_to_dict(report)
    report_dict.update({
        "kind": "improve",
        "degenerate": degenerate,
        "rounds": rounds,
        "episodes_per_round": episodes_per_round,
        "holdout_size": frozen.size,
        "final_champion_gen": result.final_champion_gen,
        "final_holdout_rate": result.final_holdout_rate,
        "final_holdout_wilson": result.final_holdout_wilson,
        "halted_reason": result.halted_reason,
        "loop_promoted": result.promoted,
    })
    return {
        "report": report_dict,
        "report_obj": report,
        "gate": gate,
        "rounds": rounds_log,
        "review_queue": review_queue,
        "episodes": store.episodes,
        "agent_label": loop.champion.agent_id,
    }
