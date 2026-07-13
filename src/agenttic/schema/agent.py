"""Declared agent catalog schema — a pre-registered agent under test.

The platform discovers agents descriptively from runs (any endpoint/config is a
new agent). The *declared catalog* is the complementary path: an operator
pre-registers an agent (its variant + connection details) so it shows up as a
pickable option when configuring a run, and so its true type can be shown on the
Agenttic Index. Discovery still applies to anything never declared.

Like rubric criteria (Hard Rule 2), invalid connection details are rejected at
model-validation time, so a declared agent that can't be built never reaches the
registry. Catalog entries are versioned and append-only in the registry (Hard
Rule 8): editing one stores the next version.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

AgentVariant = Literal["reference", "blackbox", "managed"]


class DeclaredAgent(BaseModel):
    """An agent an operator has registered for reuse. The fields mirror the
    arguments :func:`agenttic.ops.build_adapter` needs, so a declared entry maps
    one-to-one onto a runnable adapter."""

    agent_id: str
    variant: AgentVariant = "reference"
    description: str = ""
    version: int = 1

    # connection details (only the ones the chosen variant needs are required)
    model: str = ""           # reference: overrides config's agent_default
    system_prompt: str = ""   # reference: the DUT's task instructions
    url: str = ""             # blackbox: the HTTP endpoint
    managed_agent_id: str = ""    # managed: Anthropic Managed Agent id
    environment_id: str = ""      # managed: its environment id

    # black-box cost hints — black-box agents expose no token usage, so cost is
    # unknown unless declared. Provide a flat per-call cost, OR expected token
    # counts priced at `model`'s rate (else the default rate). Unset => unknown.
    cost_per_call_usd: float = 0.0
    expected_input_tokens: int = 0
    expected_output_tokens: int = 0

    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _connection_requirements(self) -> "DeclaredAgent":
        if not self.agent_id.strip():
            raise ValueError("declared agent needs a non-empty agent_id")
        if self.variant == "blackbox" and not self.url:
            raise ValueError(
                f"agent {self.agent_id}: blackbox agents require a url")
        if self.variant == "managed" and not (
                self.managed_agent_id and self.environment_id):
            raise ValueError(
                f"agent {self.agent_id}: managed agents require "
                "managed_agent_id and environment_id")
        return self
