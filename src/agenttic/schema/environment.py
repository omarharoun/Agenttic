"""Stateful environment schema (SPEC-7 Step 29.1).

A business agent mutates real state — orders, refunds, tickets. An
``Environment`` is a first-class, versioned artifact: an initial "database"
(``seed_state``) plus the tools that read and write it. The engine
(``envs/engine.py``) instantiates a fresh state per run and replays tool calls
deterministically, so goal-state verification (τ-bench's core check) is code,
not judgement (Hard Rule 33).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: declarative CRUD operations the engine can execute against the entity store
ToolOp = Literal["get", "list", "update", "create", "delete"]


class ToolSpec(BaseModel):
    """A tool bound to an environment. Declarative so it serialises and replays
    deterministically — no arbitrary code in the artifact."""

    name: str
    effect: Literal["read", "write"]
    op: ToolOp
    entity_type: str                       # which table in seed_state it touches
    input_schema: dict = Field(default_factory=dict)
    description: str = ""


class Environment(BaseModel):
    """A seed database + the tools over it. ``seed_state`` is
    ``{entity_type: {entity_id: {field: value}}}``."""

    env_id: str
    version: int = 1
    seed_state: dict = Field(default_factory=dict)
    tools: list[ToolSpec] = Field(default_factory=list)

    def tool(self, name: str) -> ToolSpec | None:
        return next((t for t in self.tools if t.name == name), None)
