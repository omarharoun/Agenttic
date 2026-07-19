"""User-scenario schema (SPEC-7 Step 30).

A conversational case is driven by a simulated user built from a `UserScenario`.
The scenario — not the transcript — is the versioned artifact: same scenario +
same simulator seed/temperature ⇒ reproducible conversations.

HONESTY (Lost-in-Simulation, arXiv:2601.17087): a simulated user is a PROXY,
not a human. Every trace it produces is labelled ``user_source="simulated"``
end to end (Hard Rule 31).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserScenario(BaseModel):
    """The brief a simulated user role-plays."""

    persona: str = ""
    goal: str
    #: facts the user knows and will share WHEN ASKED (the agent must elicit them)
    known_facts: dict = Field(default_factory=dict)
    #: facts revealed only under a specific condition (a trigger phrase/keyword)
    #: — {fact_key: {"value": ..., "reveal_when": "<substring the agent must say>"}}
    hidden_facts: dict = Field(default_factory=dict)
    temperament: str = "neutral"       # neutral | impatient | friendly | frustrated
    #: substrings whose appearance in an agent message ends the conversation
    stop_conditions: list[str] = Field(default_factory=list)
    max_turns: int = 8
