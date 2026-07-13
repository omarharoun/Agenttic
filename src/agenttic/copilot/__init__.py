"""Agenttic Copilot — the in-app, read-only guide assistant.

A platform-provided chat assistant (Claude Sonnet 4.6, called server-side with
Agenttic's own key) that helps authenticated users understand and navigate the
platform. v1 is a guide/answer copilot: Q&A + navigation deep-links, no actions.

Pieces:
- :mod:`agenttic.copilot.skill` — persona, scope, tone, guardrails (the "skill").
- ``knowledge.md`` — curated, grounded platform knowledge injected each turn.
- :mod:`agenttic.copilot.service` — build request, call Sonnet 4.6, stream + guard.
- :mod:`agenttic.copilot.credits` — the billing/free-credits integration seam.
The HTTP/SSE surface is :mod:`agenttic.server.routes.copilot`.
"""

from agenttic.copilot.agent import CopilotAgent, new_session
from agenttic.copilot.credits import (
    CreditDecision, check_credits, get_provider, record_action, record_usage,
)
from agenttic.copilot.service import (
    CopilotConfig, CopilotNotConfigured, CopilotService, is_configured,
    resolve_client,
)
from agenttic.copilot.skill import build_system_prompt, load_knowledge
from agenttic.copilot.store import CopilotStore
from agenttic.copilot.tools import ToolContext, all_tools, tool_schemas

__all__ = [
    "CopilotConfig", "CopilotNotConfigured", "CopilotService", "is_configured",
    "resolve_client", "build_system_prompt", "load_knowledge",
    "CreditDecision", "check_credits", "record_usage", "record_action",
    "get_provider", "CopilotAgent", "new_session", "CopilotStore",
    "ToolContext", "all_tools", "tool_schemas",
]
