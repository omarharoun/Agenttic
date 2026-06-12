"""Adapter base — the driver interface every agent must be wrapped in.

An adapter's single job: accept a test input, run the agent, and emit a
well-formed :class:`~ascore.schema.trace.Trace`. The harness (Step 3) only
ever talks to this interface, which is what makes "any agent, any framework"
possible.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Literal

from ascore.schema.trace import Trace


class AgentAdapter(ABC):
    """Abstract driver around an agent under test."""

    agent_id: str
    visibility: Literal["glass_box", "black_box"]

    @abstractmethod
    def describe(self) -> dict:
        """Stable description of the agent configuration (model, prompt,
        tools, ...). Used for the config hash; must be JSON-serializable
        and deterministic."""

    @abstractmethod
    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        """Execute one run and return a complete Trace. Agent mistakes must
        be captured as data (error spans), never raised (Hard Rule 5)."""

    def config_hash(self) -> str:
        """Hash of the agent configuration; ties every trace/scorecard to the
        exact agent version that produced it."""
        payload = json.dumps(self.describe(), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]
