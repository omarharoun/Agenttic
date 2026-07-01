"""
Agents.

`Agent` is the thing being trained/evaluated. Two implementations ship here:

- `HeuristicSupportAgent`: a rules-based baseline. It is intentionally *good but
  not perfect* — which is the whole point of the MVP: it lets you watch the
  guardrail refuse to promote an agent that sits below the 99% floor.

- `ModelAgent`: a stub showing exactly where a real LLM call goes. It is not
  wired to a live API in the MVP; the message format is spelled out so you can
  drop in your provider of choice.
"""

from __future__ import annotations

from typing import Any, Dict


class Agent:
    """Base agent. Implement `act`."""

    agent_id: str = "agent"

    def act(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


# --- Baseline heuristic agent -------------------------------------------------

_CATEGORY_KEYWORDS = {
    "billing": ["charge", "invoice", "refund", "payment", "billed", "subscription", "price"],
    "technical": ["error", "crash", "bug", "broken", "not working", "fails", "500", "timeout"],
    "account": ["password", "login", "log in", "sign in", "locked", "reset", "2fa", "access"],
}

_URGENT_WORDS = ["urgent", "asap", "immediately", "down", "outage", "cannot access", "can't access"]
_HIGH_WORDS = ["angry", "frustrated", "escalate", "manager", "unacceptable", "still broken"]


class HeuristicSupportAgent(Agent):
    """Keyword-rule triage. Decent coverage, deliberately imperfect on edge cases."""

    agent_id = "heuristic-support-v1"

    def act(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        text = str(observation.get("message", "")).lower()

        # Category: first keyword bucket that matches, else "other".
        category = "other"
        for cat, words in _CATEGORY_KEYWORDS.items():
            if any(w in text for w in words):
                category = cat
                break

        # Priority: urgent > high > normal, from sentiment/keywords.
        if any(w in text for w in _URGENT_WORDS):
            priority = "urgent"
        elif any(w in text for w in _HIGH_WORDS):
            priority = "high"
        else:
            priority = "normal"

        # Action: mapped from category (this is where the baseline loses points —
        # it ignores nuance like "refund already promised" or mixed-category tickets).
        action = {
            "billing": "issue_refund",
            "technical": "escalate_to_engineering",
            "account": "reset_password",
            "other": "answer_faq",
        }[category]

        return {"category": category, "priority": priority, "action": action}


# --- Model-backed agent (STUB) ------------------------------------------------

class ModelAgent(Agent):
    """STUB: an LLM-backed agent.

    Replace `act` with a real call to your model provider. The observation already
    carries a `system` instruction from the task; build messages like:

        messages = [
            {"role": "system", "content": observation["system"]},
            {"role": "user", "content": observation["message"]},
        ]
        # response = call_your_model(messages)  # must return the JSON schema below
        # return parse_json(response)

    The returned dict must match the task's action schema, e.g.
    {"category": ..., "priority": ..., "action": ...}.
    """

    agent_id = "model-agent-stub"

    def __init__(self, model: str = "your-model-here"):
        self.model = model

    def act(self, observation: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError(
            "ModelAgent is a stub. Wire it to your model API and return an action "
            "dict matching the task schema. Keep the grader and trainer unchanged."
        )
