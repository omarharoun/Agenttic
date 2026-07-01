"""
The camp.

`TrainingCamp` runs an agent against a task for N episodes, records every
episode to memory, and reports accuracy. It reports not just the raw pass-rate
but the **Wilson 95% lower bound**, because "990/1000 passed" and "99/100
passed" are not equally trustworthy, and a 99% *floor* should be judged against
the number you can actually defend, not the lucky point estimate.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass

from .agent import Agent
from .environment import Environment
from .task import Task
from .trace import Episode, TraceStore


def wilson_lower_bound(passes: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval for a binomial proportion."""
    if n == 0:
        return 0.0
    phat = passes / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


@dataclass
class CampConfig:
    episodes: int = 500
    accuracy_threshold: float = 0.99   # the hard floor
    min_episodes_for_gate: int = 200   # below this, the number is not trustworthy
    seed: int = 0


@dataclass
class CampReport:
    task_id: str
    agent_id: str
    episodes: int
    passes: int
    threshold: float
    min_episodes_for_gate: int

    @property
    def pass_rate(self) -> float:
        return self.passes / self.episodes if self.episodes else 0.0

    @property
    def wilson_lower_95(self) -> float:
        return wilson_lower_bound(self.passes, self.episodes)

    @property
    def enough_data(self) -> bool:
        return self.episodes >= self.min_episodes_for_gate

    def meets_threshold(self) -> bool:
        """Meets the floor only if we have enough data AND the *lower bound* clears it."""
        return self.enough_data and self.wilson_lower_95 >= self.threshold

    def summary(self) -> str:
        return (
            f"[{self.agent_id} @ {self.task_id}] "
            f"{self.passes}/{self.episodes} passed  "
            f"rate={self.pass_rate:.4f}  "
            f"wilson95_low={self.wilson_lower_95:.4f}  "
            f"floor={self.threshold:.2f}  "
            f"meets_floor={self.meets_threshold()}"
        )


class TrainingCamp:
    def __init__(self, task: Task, env: Environment, agent: Agent, store: TraceStore):
        self.task = task
        self.env = env
        self.agent = agent
        self.store = store

    def run(self, config: CampConfig) -> CampReport:
        passes = 0
        for _ in range(config.episodes):
            obs = self.env.reset()
            action = self.agent.act(obs)
            result = self.env.step(action)
            grade = result.info["grade"]
            case = result.info["case"]

            if grade.passed:
                passes += 1

            self.store.record(
                Episode(
                    episode_id=uuid.uuid4().hex[:12],
                    task_id=self.task.task_id,
                    agent_id=self.agent.agent_id,
                    timestamp=time.time(),
                    inputs=case.inputs,
                    action=action,
                    passed=grade.passed,
                    score=grade.score,
                    grade_detail=grade.detail,
                    system_prompt=obs.get("system", ""),
                )
            )

        return CampReport(
            task_id=self.task.task_id,
            agent_id=self.agent.agent_id,
            episodes=config.episodes,
            passes=passes,
            threshold=config.accuracy_threshold,
            min_episodes_for_gate=config.min_episodes_for_gate,
        )
