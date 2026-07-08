"""agenttic-langgraph — trace a LangGraph agent onto the Agenttic OTel bus.

Two lines for the user::

    from agenttic_langgraph import trace
    graph = trace(compiled_graph, agent_id="support-bot",
                  endpoint="https://agenttic.internal")

``trace`` returns a transparent wrapper around the compiled graph. On each
``invoke``/``stream`` it attaches a LangChain **public** callback handler
(``langchain_core.callbacks.BaseCallbackHandler``) that observes LLM and tool
events and emits OTel-GenAI spans via :class:`ascore.ingest.emit.SpanEmitter`.

Guarantees (SPEC-7 Step 36, Hard Rules 31–32):

* **Behavior-identical** — the callback only observes; it never mutates the
  graph's inputs or outputs. The wrapper delegates every other attribute to the
  wrapped graph, so it is a drop-in.
* **Public API only** — it hooks ``config["callbacks"]``, the documented
  LangChain/LangGraph extension point. No private modules, no monkey-patching.
* **Observe by default** — spans are emitted; nothing is blocked. The optional
  ``enforce=`` argument routes tool calls through the SPEC-4 gateway at the
  ramp's non-blocking default posture, and fails loudly if no compiled policy
  exists (see :func:`ascore.enforce.adapter_guard.build_enforce_guard`).
"""
from __future__ import annotations

from typing import Any

from ascore.ingest.emit import SpanEmitter

try:  # public LangChain callback base; optional at import time
    from langchain_core.callbacks import BaseCallbackHandler as _HandlerBase
    HAVE_LANGCHAIN = True
except Exception:  # pragma: no cover - exercised only where langchain is absent
    _HandlerBase = object
    HAVE_LANGCHAIN = False

__all__ = ["trace", "AgentticCallbackHandler", "HAVE_LANGCHAIN"]


class AgentticCallbackHandler(_HandlerBase):
    """A LangChain callback handler that emits OTel-GenAI spans.

    Purely observational: every method reads the event and records a span; none
    returns or mutates a value the framework would act on."""

    def __init__(self, emitter: SpanEmitter):
        super().__init__()
        self.emitter = emitter
        self._pending_prompts: list[str] = []
        self._tool_stack: list[tuple[str, Any]] = []

    # -- LLM ---------------------------------------------------------------
    def on_llm_start(self, serialized, prompts, **kwargs):  # noqa: D401
        self._pending_prompts = list(prompts or [])

    def on_chat_model_start(self, serialized, messages, **kwargs):
        # messages: list[list[BaseMessage]]; flatten to text defensively
        flat = []
        for group in messages or []:
            for m in group or []:
                flat.append(getattr(m, "content", str(m)))
        self._pending_prompts = flat

    def on_llm_end(self, response, **kwargs):
        completion = _first_generation_text(response)
        usage = _token_usage(response, kwargs)
        model = _model_name(response)
        self.emitter.emit_llm_call(
            system=_system_hint(response) or "langchain",
            model=model,
            prompt="\n".join(str(p) for p in self._pending_prompts) or None,
            completion=completion,
            input_tokens=usage.get("input"),
            output_tokens=usage.get("output"))
        self._pending_prompts = []

    def on_llm_error(self, error, **kwargs):
        self._pending_prompts = []

    # -- tools -------------------------------------------------------------
    def on_tool_start(self, serialized, input_str, **kwargs):
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        self._tool_stack.append((name, input_str))

    def on_tool_end(self, output, **kwargs):
        if self._tool_stack:
            name, args = self._tool_stack.pop()
        else:
            name, args = "tool", None
        self.emitter.emit_tool_call(tool_name=name, arguments=args, result=output)

    def on_tool_error(self, error, **kwargs):
        if self._tool_stack:
            name, args = self._tool_stack.pop()
            self.emitter.emit_tool_call(tool_name=name, arguments=args,
                                        result=f"error: {error}")


# --- helpers (defensive extraction; langchain objects vary by version) -----

def _first_generation_text(response) -> str | None:
    gens = getattr(response, "generations", None)
    if gens:
        try:
            g = gens[0][0]
            return getattr(g, "text", None) or getattr(
                getattr(g, "message", None), "content", None)
        except (IndexError, TypeError):
            return None
    return None


def _token_usage(response, kwargs) -> dict:
    out = getattr(response, "llm_output", None) or {}
    usage = (out.get("token_usage") or out.get("usage") or {}) if isinstance(out, dict) else {}
    return {
        "input": usage.get("prompt_tokens") or usage.get("input_tokens"),
        "output": usage.get("completion_tokens") or usage.get("output_tokens"),
    }


def _model_name(response) -> str:
    out = getattr(response, "llm_output", None) or {}
    if isinstance(out, dict):
        return out.get("model_name") or out.get("model") or ""
    return ""


def _system_hint(response) -> str:
    out = getattr(response, "llm_output", None) or {}
    if isinstance(out, dict):
        return out.get("system") or ""
    return ""


class _TracedGraph:
    """Transparent wrapper: delegates everything to the graph, but injects the
    tracing callback on invoke/stream/ainvoke."""

    def __init__(self, graph, *, agent_id: str, agent_config_hash: str = "",
                 endpoint: str | None = None, auth_header: str | None = None,
                 sink: list | None = None, enforce_guard=None):
        self._graph = graph
        self._agent_id = agent_id
        self._agent_config_hash = agent_config_hash
        self._endpoint = endpoint
        self._auth_header = auth_header
        self._sink = sink
        self._enforce_guard = enforce_guard

    def _new_handler(self) -> AgentticCallbackHandler:
        emitter = SpanEmitter(
            self._agent_id, agent_config_hash=self._agent_config_hash,
            endpoint=self._endpoint, auth_header=self._auth_header,
            sink=self._sink, scope_name="agenttic_langgraph")
        return AgentticCallbackHandler(emitter)

    @staticmethod
    def _inject(config, handler):
        config = dict(config or {})
        cbs = config.get("callbacks")
        if cbs is None:
            config["callbacks"] = [handler]
        elif isinstance(cbs, list):
            config["callbacks"] = [*cbs, handler]
        else:  # a CallbackManager — public add_handler
            try:
                cbs.add_handler(handler)
            except Exception:
                config["callbacks"] = [handler]
        return config

    def invoke(self, input, config=None, **kwargs):
        handler = self._new_handler()
        if self._enforce_guard is not None:
            self._enforce_guard.begin()
        try:
            return self._graph.invoke(input, config=self._inject(config, handler),
                                      **kwargs)
        finally:
            handler.emitter.flush()
            if self._enforce_guard is not None:
                self._enforce_guard.end()

    def stream(self, input, config=None, **kwargs):
        handler = self._new_handler()
        try:
            yield from self._graph.stream(
                input, config=self._inject(config, handler), **kwargs)
        finally:
            handler.emitter.flush()

    async def ainvoke(self, input, config=None, **kwargs):
        handler = self._new_handler()
        try:
            return await self._graph.ainvoke(
                input, config=self._inject(config, handler), **kwargs)
        finally:
            handler.emitter.flush()

    def __getattr__(self, item):  # transparent delegation
        return getattr(self._graph, item)


def trace(graph, *, agent_id: str = "langgraph-agent", agent_config_hash: str = "",
          endpoint: str | None = None, auth_header: str | None = None,
          sink: list | None = None, enforce: Any = None, reg=None, cfg=None):
    """Wrap a compiled LangGraph graph so its runs emit OTel-GenAI spans.

    ``enforce`` is off by default (pure observation). When set, tool calls are
    routed through the SPEC-4 gateway at the ramp's non-blocking default posture;
    a missing compiled policy fails loudly (T36.3)."""
    guard = None
    if enforce:
        from ascore.enforce.adapter_guard import build_enforce_guard
        guard = build_enforce_guard(agent_id, enforce, reg=reg, cfg=cfg)
    return _TracedGraph(graph, agent_id=agent_id,
                        agent_config_hash=agent_config_hash, endpoint=endpoint,
                        auth_header=auth_header, sink=sink, enforce_guard=guard)
