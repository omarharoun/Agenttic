"""Adversarial test-case GENERATOR — the "sparring partner".

Reads a target agent's declared interface (tools + system prompt + secrets) and
emits scoreable attack :class:`~agenttic.schema.testcase.TestCase` objects, each
with a filled deterministic oracle, then runs them through the existing adapter +
scorer, keeps the ones that break the agent, mutates around the winners, and
promotes them into a versioned regression suite via the existing hardening path.

See ``README.md`` in this package for the full flow.
"""

from __future__ import annotations

from .authors import (
    Author,
    LLMRedTeamAuthor,
    NoRedTeamModel,
    TemplateAuthor,
)
from .demo_target import build_demo_target
from .descriptor import (
    AgentDescriptor,
    ToolSpec,
    reference_descriptor,
    resolve_target,
)
from .generator import AttackGenerator, ProbeResult, run_generation
from .probe import ATTACK_RUBRIC_ID, AttackSpec, Probe, attack_rubric, build_test_case

__all__ = [
    "AgentDescriptor",
    "ToolSpec",
    "reference_descriptor",
    "resolve_target",
    "Author",
    "TemplateAuthor",
    "LLMRedTeamAuthor",
    "NoRedTeamModel",
    "AttackSpec",
    "Probe",
    "attack_rubric",
    "build_test_case",
    "ATTACK_RUBRIC_ID",
    "AttackGenerator",
    "ProbeResult",
    "run_generation",
    "build_demo_target",
]
