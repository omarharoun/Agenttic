"""
The truth anchor.

`FrozenHoldout` samples a set of cases *once* (with real gold labels) and freezes
them. Every candidate agent is measured against this exact set, and this set is
NEVER used to generate training data. It is the fixed yardstick that tells you
whether a new generation is genuinely better or just gaming the training signal.

If you take one idea from the whole project: this is the thing that keeps a
self-improving loop honest. Remove it and the loop optimizes against itself.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List

from .agent import Agent
from .task import Case, Task
from .trainer import wilson_lower_bound


@dataclass
class HoldoutResult:
    n: int
    passes: int

    @property
    def pass_rate(self) -> float:
        return self.passes / self.n if self.n else 0.0

    @property
    def wilson_lower_95(self) -> float:
        return wilson_lower_bound(self.passes, self.n)


class FrozenHoldout:
    def __init__(self, task: Task, n: int, seed: int):
        # Use a dedicated seed so holdout cases never overlap the training stream.
        rng = random.Random(seed)
        self.task = task
        self._cases: List[Case] = [task.sample_case(rng) for _ in range(n)]
        # Fingerprint so tests / callers can assert the set is never mutated.
        self.fingerprint = tuple(c.case_id for c in self._cases)

    @property
    def size(self) -> int:
        return len(self._cases)

    def evaluate(self, agent: Agent) -> HoldoutResult:
        passes = 0
        system = self.task.system_prompt()
        for case in self._cases:
            obs = {"system": system, **case.inputs}
            action = agent.act(obs)
            if self.task.grade(case, action).passed:
                passes += 1
        return HoldoutResult(n=len(self._cases), passes=passes)

    def failing_cases(self, agent: Agent):
        """Cases the agent still gets wrong — the human review / teaching queue."""
        system = self.task.system_prompt()
        for case in self._cases:
            obs = {"system": system, **case.inputs}
            action = agent.act(obs)
            grade = self.task.grade(case, action)
            if not grade.passed:
                yield case, action, grade
