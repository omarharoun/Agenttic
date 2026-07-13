"""
Environments.

The `Environment` interface is deliberately RL-shaped (`reset` / `step`) so the
same training loop works whether the agent is answering a support ticket, or
driving a real browser, or tapping a real Android screen.

- `MockSupportEnv` is fully deterministic and offline, so the MVP runs anywhere
  and every number is reproducible.
- `BrowserEnvironment` and `AndroidEnvironment` are *stubs*. They are not faked —
  they raise until you implement the real integration (Playwright / Appium).
  This is where the "train on real phones and browsers thousands of times" idea
  plugs in without touching the trainer, grader, or memory code.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .task import Case, Task


@dataclass
class StepResult:
    observation: Dict[str, Any]
    reward: float
    done: bool
    info: Dict[str, Any]


class Environment:
    """Base environment. Subclass and implement `reset` and `step`."""

    def reset(self) -> Dict[str, Any]:
        raise NotImplementedError

    def step(self, action: Dict[str, Any]) -> StepResult:
        raise NotImplementedError


class MockSupportEnv(Environment):
    """A single-step environment for a support-triage `Task`.

    reset()  -> presents one customer message (the observation)
    step()   -> grades the agent's decision and returns reward + the grade
    """

    def __init__(self, task: Task, rng: random.Random):
        self.task = task
        self.rng = rng
        self._case: Optional[Case] = None

    def reset(self) -> Dict[str, Any]:
        self._case = self.task.sample_case(self.rng)
        # Observation is exactly what the agent is allowed to see (never `gold`).
        return {"system": self.task.system_prompt(), **self._case.inputs}

    def step(self, action: Dict[str, Any]) -> StepResult:
        if self._case is None:
            raise RuntimeError("Call reset() before step().")
        grade = self.task.grade(self._case, action)
        return StepResult(
            observation={},
            reward=grade.score,
            done=True,
            info={"case": self._case, "grade": grade},
        )


class BrowserEnvironment(Environment):
    """STUB: real browser env (e.g. Playwright).

    Implement `reset` to open the target page and return a text/DOM observation,
    and `step` to execute the agent's action (click/type/navigate) and grade the
    resulting page state. The trainer and memory layers need no changes.
    """

    def reset(self) -> Dict[str, Any]:  # pragma: no cover - intentional stub
        raise NotImplementedError(
            "BrowserEnvironment is a stub. Wire up Playwright: launch a browser, "
            "load the task page, and return the observable state here."
        )

    def step(self, action: Dict[str, Any]) -> StepResult:  # pragma: no cover
        raise NotImplementedError(
            "BrowserEnvironment is a stub. Execute the browser action and grade "
            "the resulting page state against the task's gold criteria."
        )


class AndroidEnvironment(Environment):
    """STUB: real Android env (e.g. Appium / a device farm).

    Same contract as above, against a real or emulated device. This is where
    'thousands of runs on actual Android phones' would live.
    """

    def reset(self) -> Dict[str, Any]:  # pragma: no cover - intentional stub
        raise NotImplementedError(
            "AndroidEnvironment is a stub. Connect to a device/emulator, launch "
            "the app under test, and return the screen state as the observation."
        )

    def step(self, action: Dict[str, Any]) -> StepResult:  # pragma: no cover
        raise NotImplementedError(
            "AndroidEnvironment is a stub. Perform the tap/type action on device "
            "and grade the resulting screen against the task's gold criteria."
        )
