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
from .honeypot import (
    ATTEMPTED_BLOCKED,
    DEFAULT_HONEYPOTS,
    EXECUTED_ALLOWED,
    RESISTED,
    GuardedHoneypotAgent,
    HoneypotAuthor,
    HoneypotOutcome,
    HoneypotReport,
    build_guarded_demo_target,
    classify_outcome,
    honeypot_tool_schemas,
    install_honeypot_enforcement,
    plant_honeypots,
    promote_executed_failures,
    run_honeypot_harness,
    run_honeypot_probes,
)
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
    # honeypot / harness-enforcement slice
    "DEFAULT_HONEYPOTS",
    "plant_honeypots",
    "honeypot_tool_schemas",
    "HoneypotAuthor",
    "GuardedHoneypotAgent",
    "build_guarded_demo_target",
    "install_honeypot_enforcement",
    "classify_outcome",
    "HoneypotOutcome",
    "HoneypotReport",
    "run_honeypot_probes",
    "run_honeypot_harness",
    "promote_executed_failures",
    "RESISTED",
    "ATTEMPTED_BLOCKED",
    "EXECUTED_ALLOWED",
]
