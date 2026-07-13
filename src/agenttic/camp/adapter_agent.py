"""
Bridge: run a *real* Agenttic agent inside a training camp.

The vendored camp engine only knows the tiny ``Agent`` protocol (``act(obs) ->
action_dict``). Agenttic already knows how to drive any agent — the tenant's
BYO-Anthropic-key reference agent, a managed agent, or a black-box HTTP endpoint
— behind :class:`agenttic.adapters.base.AgentAdapter` (``run(input) -> Trace``).

``AdapterAgent`` adapts the latter to the former: it turns a camp observation
into an adapter ``test_input``, runs the real agent, and parses the agent's
free-text ``final_output`` into the structured action the deterministic grader
needs.

Honesty posture (kept from AgentCamp's README): when the real agent's output
can't be parsed into an action, we DO NOT invent one — we return an empty action
which the grader records as a failure and keeps in the trace. No faked accuracy.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from agenttic.adapters.base import AgentAdapter

from .agent import Agent

_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)


def parse_action(text: str) -> Dict[str, Any]:
    """Best-effort extraction of a JSON action object from an agent's reply.

    Returns ``{}`` when nothing parseable is found — deliberately, so a
    non-conforming answer is graded as a miss rather than silently excused.
    """
    if not text:
        return {}
    # Fast path: the whole reply is JSON.
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        pass
    # Fallback: first {...} span in the reply (handles ```json fences, prose).
    m = _JSON_OBJ.search(text)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


class AdapterAgent(Agent):
    """Wrap an Agenttic :class:`AgentAdapter` as a camp ``Agent``."""

    def __init__(self, adapter: AgentAdapter, agent_id: str | None = None):
        self.adapter = adapter
        self.agent_id = agent_id or getattr(adapter, "agent_id", "byo-agent")

    def act(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        # The observation carries the task's system instruction + case inputs
        # (never the gold). Hand both to the adapter; keep the message primary.
        message = observation.get("message")
        test_input: Dict[str, Any] = dict(observation)
        if message is not None:
            test_input.setdefault("request", message)
        trace = self.adapter.run(test_input)
        return parse_action(trace.final_output)
