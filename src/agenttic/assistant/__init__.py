"""The Safe Reference Assistant — agenttic's flagship dogfood agent.

A safety-hardened, sandboxed, tool-using personal assistant built to PASS
agenttic's own Safety Battery. It is the proof that "safe to deploy" is real:
it demonstrates, by construction, the defenses an OpenClaw-class agent lacks —

* **Untrusted-content / prompt-injection resistance** (:mod:`.guard`): every
  tool/external result is wrapped and treated as DATA, never instructions; a
  guard neutralizes embedded "ignore previous instructions"-style directives
  before the model ever sees them as commands.
* **Sandboxed, allowlisted tools only** (:mod:`.tools`): a tiny SAFE tool set
  (calculator, per-session notes, SSRF-guarded web_fetch). No host filesystem,
  no shell, no credential access, no arbitrary code — the blast radius is
  contained by construction; anything off the allowlist is default-denied.
* **Human-in-the-loop approval gate** (:mod:`.agent`): any tool tagged
  *sensitive* PAUSES the loop and requires explicit user approval before it
  runs (the IBM control-boundary pattern).
* **No secret leakage** (:mod:`.guard`): the assistant has no secret tools, and
  an output filter scrubs anything that looks like a key/secret from its replies.

The :class:`~agenttic.assistant.adapter.SafeAssistantAgent` wraps the loop as a
black-box :class:`~agenttic.adapters.base.AgentAdapter` so the coordinator can run
this assistant through agenttic's own safety battery to self-certify it.
"""

from __future__ import annotations

from agenttic.assistant.agent import SafeAssistant
from agenttic.assistant.posture import safety_posture

__all__ = ["SafeAssistant", "safety_posture"]
