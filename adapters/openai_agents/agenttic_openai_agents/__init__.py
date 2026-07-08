"""agenttic-openai-agents — trace an OpenAI Agents SDK agent onto the OTel bus.

Two lines for the user::

    from agenttic_openai_agents import trace
    agent = trace(my_agent, agent_id="triage", endpoint="https://agenttic.internal")
    result = await agent.run("hello")

``trace`` returns a transparent wrapper around the agent whose ``run``/``run_sync``
inject an Agenttic ``RunHooks`` instance into ``Runner.run(...)`` — the SDK's
**public** lifecycle-hook extension point. The hooks observe LLM and tool events
and emit OTel-GenAI spans via :class:`ascore.ingest.emit.SpanEmitter`. Users who
drive ``Runner`` themselves can pass :class:`AgentticRunHooks` directly.

Guarantees (SPEC-7 Step 36, Hard Rules 31–32): behavior-identical (hooks only
observe; the wrapper delegates all else to the agent), public-API-only (no
private-module imports, no monkey-patching), observe-by-default with an optional
non-blocking ``enforce=`` guard that fails loud without a compiled policy.
"""
from __future__ import annotations

from typing import Any

from ascore.ingest.emit import SpanEmitter

try:  # public RunHooks lifecycle base; optional at import time
    from agents import RunHooks as _HooksBase
    HAVE_OPENAI_AGENTS = True
except Exception:  # pragma: no cover - exercised only where the SDK is absent
    _HooksBase = object
    HAVE_OPENAI_AGENTS = False

__all__ = ["trace", "AgentticRunHooks", "HAVE_OPENAI_AGENTS"]


class AgentticRunHooks(_HooksBase):
    """OpenAI Agents SDK ``RunHooks`` that emit OTel-GenAI spans.

    The lifecycle methods are async (as the SDK expects) and purely
    observational — they record a span and return None; they never alter the run
    result. Signatures accept ``*args, **kwargs`` defensively so the adapter is
    resilient to minor SDK version differences without reaching into internals."""

    def __init__(self, emitter: SpanEmitter):
        super().__init__()
        self.emitter = emitter
        self._pending: dict[int, Any] = {}

    async def on_llm_end(self, context, agent, response, *args, **kwargs):
        usage = _usage(response)
        self.emitter.emit_llm_call(
            system="openai_agents",
            model=_model(agent, response),
            completion=_output_text(response),
            input_tokens=usage.get("input"),
            output_tokens=usage.get("output"))

    async def on_tool_start(self, context, agent, tool, *args, **kwargs):
        self._pending[id(tool)] = _tool_name(tool)

    async def on_tool_end(self, context, agent, tool, result=None, *args, **kwargs):
        name = self._pending.pop(id(tool), None) or _tool_name(tool)
        self.emitter.emit_tool_call(tool_name=name, result=result)

    def flush(self):
        return self.emitter.flush()


# --- defensive extraction --------------------------------------------------

def _tool_name(tool) -> str:
    return getattr(tool, "name", None) or getattr(tool, "__name__", None) or "tool"


def _model(agent, response) -> str:
    return (getattr(response, "model", None)
            or getattr(agent, "model", None) and str(getattr(agent, "model"))
            or "")


def _output_text(response) -> str | None:
    for attr in ("output_text", "final_output", "content", "text"):
        v = getattr(response, attr, None)
        if v:
            return str(v)
    return None


def _usage(response) -> dict:
    u = getattr(response, "usage", None)
    if u is None:
        return {}
    return {
        "input": getattr(u, "input_tokens", None) or getattr(u, "prompt_tokens", None),
        "output": getattr(u, "output_tokens", None) or getattr(u, "completion_tokens", None),
    }


class _TracedAgent:
    """Transparent wrapper delegating to the agent; injects tracing hooks on run."""

    def __init__(self, agent, *, agent_id: str, agent_config_hash: str = "",
                 endpoint: str | None = None, auth_header: str | None = None,
                 sink: list | None = None, enforce_guard=None):
        self._agent = agent
        self._agent_id = agent_id
        self._agent_config_hash = agent_config_hash
        self._endpoint = endpoint
        self._auth_header = auth_header
        self._sink = sink
        self._enforce_guard = enforce_guard

    def make_hooks(self) -> AgentticRunHooks:
        emitter = SpanEmitter(
            self._agent_id, agent_config_hash=self._agent_config_hash,
            endpoint=self._endpoint, auth_header=self._auth_header,
            sink=self._sink, scope_name="agenttic_openai_agents")
        return AgentticRunHooks(emitter)

    async def run(self, input, **kwargs):
        from agents import Runner  # public entrypoint
        hooks = self.make_hooks()
        if self._enforce_guard is not None:
            self._enforce_guard.begin()
        try:
            return await Runner.run(self._agent, input, hooks=hooks, **kwargs)
        finally:
            hooks.flush()
            if self._enforce_guard is not None:
                self._enforce_guard.end()

    def run_sync(self, input, **kwargs):
        from agents import Runner
        hooks = self.make_hooks()
        try:
            return Runner.run_sync(self._agent, input, hooks=hooks, **kwargs)
        finally:
            hooks.flush()

    def __getattr__(self, item):
        return getattr(self._agent, item)


def trace(agent, *, agent_id: str = "openai-agent", agent_config_hash: str = "",
          endpoint: str | None = None, auth_header: str | None = None,
          sink: list | None = None, enforce: Any = None, reg=None, cfg=None):
    """Wrap an OpenAI Agents SDK agent so its runs emit OTel-GenAI spans.

    ``enforce`` is off by default. When set, tool calls route through the SPEC-4
    gateway at the ramp's non-blocking default posture; a missing compiled policy
    fails loudly (T36.3)."""
    guard = None
    if enforce:
        from ascore.enforce.adapter_guard import build_enforce_guard
        guard = build_enforce_guard(agent_id, enforce, reg=reg, cfg=cfg)
    return _TracedAgent(agent, agent_id=agent_id,
                        agent_config_hash=agent_config_hash, endpoint=endpoint,
                        auth_header=auth_header, sink=sink, enforce_guard=guard)
