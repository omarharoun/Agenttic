"""
Task abstractions.

A `Task` is one thing you want to specialize an agent at (e.g. "support triage",
"fix a failing test"). It knows how to (a) sample a fresh, concrete `Case` and
(b) grade an agent's answer to that case deterministically.

Determinism matters: if the grader is noisy you cannot trust a 99% number.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, Protocol


@dataclass(frozen=True)
class Case:
    """A single concrete instance of a task.

    `inputs` is what the agent gets to see. `gold` is the hidden correct answer,
    used only by the grader — the agent never sees it.
    """
    case_id: str
    inputs: Dict[str, Any]
    gold: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GradeResult:
    """Outcome of grading one attempt.

    `passed` is the pass/fail bit that feeds the accuracy number.
    `score` is a continuous [0, 1] signal useful for reward/debugging.
    `detail` explains *why*, which is what makes traces worth keeping.
    """
    passed: bool
    score: float
    detail: Dict[str, Any] = field(default_factory=dict)


class Task(Protocol):
    """The contract a trainable skill must satisfy."""

    task_id: str
    name: str

    def system_prompt(self) -> str:
        """Instruction shown to the agent for every case of this task."""
        ...

    def sample_case(self, rng: random.Random) -> Case:
        """Draw a fresh case. Should cover the real distribution, incl. hard ones."""
        ...

    def grade(self, case: Case, action: Dict[str, Any]) -> GradeResult:
        """Deterministically grade `action` against `case.gold`."""
        ...
