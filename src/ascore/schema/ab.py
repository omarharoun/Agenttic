"""A/B comparison schema — two agent variants, head-to-head on one suite.

A **variant** is an agent-under-test configuration. It mirrors the arguments
:func:`ascore.ops.build_adapter` needs (same fields as a declared agent), so any
of three comparisons fall out of the same abstraction:

* two different agents (different ``agent_id`` / ``url`` / managed ids),
* the same agent on two **models** (same ``agent_id``, different ``model``),
* the same agent with two **system prompts** (same ``agent_id``, different
  ``system_prompt``) — the case the prompt-optimizer roadmap is built on.

The :class:`ABComparison` is the paired-comparison artifact: both variants'
scorecards, the per-case pass/fail diff, per-criterion deltas, cost/latency, and
a statistically honest verdict (see :mod:`ascore.stats`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ascore.schema.agent import AgentVariant


class ABVariant(BaseModel):
    """One agent-under-test configuration in an A/B comparison. Maps one-to-one
    onto a runnable adapter; the same connection-detail validation as a declared
    agent applies (an unbuildable variant never starts a run)."""

    label: str = "A"                 # display name ("A"/"B" or a human label)
    variant: AgentVariant = "reference"
    agent_id: str = "agent-under-test"

    model: str = ""                  # reference: overrides config's agent_default
    system_prompt: str = ""          # reference: the DUT's task instructions
    url: str = ""                    # blackbox: the HTTP endpoint
    headers: dict = Field(default_factory=dict)
    managed_agent_id: str = ""
    environment_id: str = ""

    cost_per_call_usd: float = 0.0
    expected_input_tokens: int = 0
    expected_output_tokens: int = 0

    @model_validator(mode="after")
    def _connection_requirements(self) -> "ABVariant":
        if not self.agent_id.strip():
            raise ValueError("variant needs a non-empty agent_id")
        if self.variant == "blackbox" and not self.url:
            raise ValueError(f"variant {self.label}: blackbox agents require a url")
        if self.variant == "managed" and not (
                self.managed_agent_id and self.environment_id):
            raise ValueError(
                f"variant {self.label}: managed agents require "
                "managed_agent_id and environment_id")
        return self

    def summary(self) -> str:
        """One-line human description of what makes this variant distinct."""
        bits = [self.agent_id]
        if self.variant != "reference":
            bits.append(self.variant)
        if self.model:
            bits.append(f"model={self.model}")
        if self.system_prompt:
            bits.append("custom prompt")
        if self.url:
            bits.append(self.url)
        return " · ".join(bits)


class CriterionComparison(BaseModel):
    """Per-criterion A-vs-B delta with a paired-bootstrap verdict."""
    criterion_id: str
    mean_a: float
    mean_b: float
    delta: float                 # mean_b - mean_a (>0 favors B)
    direction: str               # "A" | "B" | "tie"
    p_value: float
    ci_low: float
    ci_high: float
    significant: bool
    n: int                       # paired cases scored on this criterion by both


class FlippedCase(BaseModel):
    """A case whose pass/fail outcome changed between the variants."""
    test_id: str
    a_passed: bool
    b_passed: bool
    direction: Literal["gain", "loss"]   # gain: A fail -> B pass; loss: reverse


class ABComparison(BaseModel):
    """The result of running two variants on the same suite — paired, same
    rubric, same judge. The only thing that differs between the runs is the
    variant, so the deltas are attributable to it."""

    comparison_id: str
    suite_id: str
    suite_version: int
    rubric_id: str
    rubric_version: int

    label_a: str
    label_b: str
    variant_a: ABVariant
    variant_b: ABVariant
    scorecard_a_id: str
    scorecard_b_id: str

    n_paired: int                        # cases scored by BOTH variants
    excluded_test_ids: list[str] = Field(default_factory=list)  # errored in either

    # binary success comparison (over the paired subset — same cases for both)
    success_rate_a: float
    success_rate_b: float
    success_delta: float                 # rate_b - rate_a
    mcnemar: dict                        # ascore.stats.McNemarResult.to_dict()

    per_criterion: list[CriterionComparison] = Field(default_factory=list)
    flipped_cases: list[FlippedCase] = Field(default_factory=list)

    # execution metrics (each variant's own scorecard aggregates)
    mean_cost_a: float = 0.0
    mean_cost_b: float = 0.0
    total_cost_a: float = 0.0
    total_cost_b: float = 0.0
    p95_latency_a: float = 0.0
    p95_latency_b: float = 0.0

    winner: Literal["A", "B", "tie"]
    verdict: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
