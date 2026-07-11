"""Auto-detecting ``trace()`` (SPEC-8 Step 41).

One import for everyone::

    from agenttic import trace
    agent = trace(anything)

``trace`` inspects the object's **public shape** and dispatches to the right
adapter without the caller ever naming the framework:

* a LangGraph compiled graph  → the ``agenttic-langgraph`` adapter
* an OpenAI Agents SDK agent   → the ``agenttic-openai-agents`` adapter
* anything else callable       → a generic OTel-emitting wrapper (Step 42's
  mechanism, :func:`agenttic._decorator` — a partial-trajectory canonical run)

Detection is duck-typed on documented type/attribute signatures — we never
import a framework's private modules, and we never import the framework SDK just
to *detect* (so a fixture that merely mimics the shape dispatches correctly, and
a missing SDK is simply "not this one"). Loading the matched adapter is behind
``try/except ImportError``: if the object looks like LangGraph but
``agenttic-langgraph`` isn't installed, we fall back to the generic wrapper
rather than crash. The resolution order and the matched adapter are logged at
DEBUG.

Guarantees: behavior-identical wrapping (Hard Rule 38 — observe, never block or
mutate), and no telemetry by default (Hard Rule 38 — with no target configured
it is a no-op that logs where to set one; it never phones home). ``enforce=True``
opts into the SPEC-4 gateway at the ramp's non-blocking default posture.
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Any

log = logging.getLogger("agenttic")

_GUIDANCE_LOGGED = False


# --- target resolution / no-phone-home guidance (Hard Rule 38) -------------
def _config_get(cfg: Any, *keys: str) -> Any:
    """Read cfg['distribution'][key] from a passed dict, else from the config
    file named by AGENTTIC_CONFIG, else None. Never raises."""
    for key in keys:
        if isinstance(cfg, dict):
            val = (cfg.get("distribution") or {}).get(key)
            if val:
                return val
    path = os.environ.get("AGENTTIC_CONFIG")
    if path and os.path.exists(path):
        try:
            from ascore.config import load_config
            dist = (load_config(path).get("distribution") or {})
            for key in keys:
                if dist.get(key):
                    return dist[key]
        except Exception:  # noqa: BLE001 — config is best-effort for the library
            return None
    return None


def _resolve_target(target: str | None, cfg: Any = None) -> str | None:
    """Where spans go, in priority order: explicit ``target`` arg → the
    AGENTTIC_TARGET / OTEL_EXPORTER_OTLP_ENDPOINT env vars → the ``distribution.
    target`` config key (dict or AGENTTIC_CONFIG file). None => don't emit."""
    if target:
        return target
    env = (os.environ.get("AGENTTIC_TARGET")
           or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    if env:
        return env
    return _config_get(cfg, "target")


# Non-blocking postures only — blocking ones (enforce_reads/enforce_all) must be
# ramped up deliberately server-side, never turned on by a library flag (Rules
# 31, 35). build_enforce_guard rejects a blocking posture loudly; we surface the
# same contract here so `trace(enforce=...)` fails fast with a clear message.
_NON_BLOCKING_POSTURES = {"observe", "shadow"}


def _resolve_enforce(enforce: Any, cfg: Any = None) -> Any:
    """Resolve the opt-in enforce posture. ``False`` → off. ``True`` → the
    configured ``distribution.enforce_posture`` (default ``shadow``). An explicit
    string is validated as non-blocking. Returns the posture string, or False."""
    if not enforce:
        return False
    posture = (_config_get(cfg, "enforce_posture") or "shadow") \
        if enforce is True else str(enforce)
    if posture not in _NON_BLOCKING_POSTURES:
        raise ValueError(
            f"agenttic.trace: enforce posture {posture!r} is blocking; the "
            "library only permits non-blocking postures "
            f"({', '.join(sorted(_NON_BLOCKING_POSTURES))}). Ramp up blocking "
            "enforcement deliberately via the gateway, not a library flag.")
    return posture


def _guidance_once() -> None:
    """Log — exactly once — how to configure a target. No silent phone-home:
    with no target and no sink, wrapping still runs the agent but emits nothing,
    and the user is told how to turn telemetry on (Hard Rule 38)."""
    global _GUIDANCE_LOGGED
    if _GUIDANCE_LOGGED:
        return
    log.warning(
        "agenttic.trace: no target configured — the wrapped agent runs normally "
        "but NO telemetry is emitted (nothing leaves this process). To send runs "
        "to your Agenttic instance, pass target='https://your-agenttic/v1/traces' "
        "or set the AGENTTIC_TARGET (or OTEL_EXPORTER_OTLP_ENDPOINT) env var.")
    _GUIDANCE_LOGGED = True


# --- framework detection (public shape only; never a private import) -------
def _type_name(obj: Any) -> str:
    try:
        return type(obj).__name__
    except Exception:  # pragma: no cover - exotic objects
        return ""


def is_langgraph(obj: Any) -> bool:
    """A LangGraph compiled graph: the public ``CompiledStateGraph`` / ``Pregel``
    type name, or the LangChain Runnable shape (``invoke`` + ``stream`` +
    ``get_graph`` callables) that a compiled graph exposes."""
    if _type_name(obj) in {"CompiledStateGraph", "CompiledGraph", "Pregel"}:
        return True
    return all(callable(getattr(obj, m, None))
               for m in ("invoke", "stream", "get_graph"))


def is_openai_agent(obj: Any) -> bool:
    """An OpenAI Agents SDK ``Agent``: its public attribute shape
    (``instructions`` + ``tools`` + ``handoffs`` + ``name``). ``Runner`` is
    separate; users pass the agent."""
    if _type_name(obj) == "Agent" and hasattr(obj, "instructions") \
            and hasattr(obj, "tools"):
        return True
    return (hasattr(obj, "instructions") and hasattr(obj, "tools")
            and hasattr(obj, "handoffs") and hasattr(obj, "name"))


def _missing_extra(framework: str, extra: str, type_name: str) -> ImportError:
    """A recognized framework object with no adapter installed: an actionable
    error (install the extra), never an opaque crash."""
    return ImportError(
        f"agenttic.trace: this looks like a {framework} object ({type_name}), "
        f"but the {framework} adapter isn't installed. Install it with:  "
        f"pip install 'agenttic[{extra}]'")


def _load_adapter(module_name: str):
    """Import a framework adapter, or return None if it isn't installed
    (a missing SDK is "not available", never a crash)."""
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


# --- the one public entry point -------------------------------------------
def trace(agent: Any, *, target: str | None = None, enforce: Any = False,
          agent_id: str | None = None, agent_config_hash: str = "",
          auth_header: str | None = None, sink: list | None = None,
          reg: Any = None, cfg: Any = None) -> Any:
    """Wrap ``agent`` so its runs emit a canonical Agenttic run — dispatching to
    the matching framework adapter by shape, or to a generic wrapper.

    ``target`` selects where spans go (defaults to the AGENTTIC_TARGET /
    OTEL_EXPORTER_OTLP_ENDPOINT env vars); with nothing configured wrapping is a
    logged no-op that never phones home. ``enforce=True`` routes tool calls
    through the SPEC-4 gateway at the ramp's non-blocking default posture (opt-in
    only). ``sink`` captures the OTLP payload in-process (tests / air-gapped)."""
    endpoint = _resolve_target(target, cfg)
    enforce = _resolve_enforce(enforce, cfg)
    if enforce:
        log.debug("agenttic.trace: enforce posture = %s (non-blocking, opt-in)",
                  enforce)
    if endpoint is None and sink is None:
        _guidance_once()

    if is_langgraph(agent):
        adapter = _load_adapter("agenttic_langgraph")
        if adapter is not None:
            log.debug("agenttic.trace: matched LangGraph adapter for %s",
                      _type_name(agent))
            return adapter.trace(
                agent, agent_id=agent_id or "langgraph-agent",
                agent_config_hash=agent_config_hash, endpoint=endpoint,
                auth_header=auth_header, sink=sink, enforce=enforce,
                reg=reg, cfg=cfg)
        if not callable(agent):
            raise _missing_extra("LangGraph", "langgraph", _type_name(agent))
        log.debug("agenttic.trace: %s looks like LangGraph but agenttic-langgraph "
                  "is not installed — using the generic wrapper", _type_name(agent))
    elif is_openai_agent(agent):
        adapter = _load_adapter("agenttic_openai_agents")
        if adapter is not None:
            log.debug("agenttic.trace: matched OpenAI Agents adapter for %s",
                      _type_name(agent))
            return adapter.trace(
                agent, agent_id=agent_id or "openai-agent",
                agent_config_hash=agent_config_hash, endpoint=endpoint,
                auth_header=auth_header, sink=sink, enforce=enforce,
                reg=reg, cfg=cfg)
        if not callable(agent):
            raise _missing_extra("OpenAI Agents", "openai", _type_name(agent))
        log.debug("agenttic.trace: %s looks like OpenAI Agents but "
                  "agenttic-openai-agents is not installed — using the generic "
                  "wrapper", _type_name(agent))

    log.debug("agenttic.trace: no known framework matched %s — generic OTel "
              "wrapper (partial trajectory)", _type_name(agent))
    return _generic_trace(
        agent, agent_id=agent_id or "agent",
        agent_config_hash=agent_config_hash, endpoint=endpoint,
        auth_header=auth_header, sink=sink, enforce=enforce, reg=reg, cfg=cfg)


# --- generic fallback: wrap an arbitrary callable --------------------------
_GENERIC_PARTIAL_REASON = (
    "generic wrapper: the callable's internal tool/LLM calls are not observable, "
    "so the tool trajectory is recorded as partial (never fabricated)")


def _wrap_callable(fn: Any, *, agent_id: str, agent_config_hash: str = "",
                   endpoint: str | None = None, auth_header: str | None = None,
                   sink: list | None = None, enforce: Any = False,
                   reg: Any = None, cfg: Any = None, scope_name: str = "agenttic_generic"):
    """Return a behavior-identical wrapper around ``fn`` that emits one
    partial-trajectory canonical run per call. Shared by the generic ``trace()``
    fallback and the ``@instrument`` decorator (Step 42)."""
    import functools
    import time

    from ascore.ingest.emit import SpanEmitter

    def _emit(inp: Any, out: Any, err: Exception | None, t0: int) -> None:
        latency_ms = (time.time_ns() - t0) / 1e6
        emitter = SpanEmitter(agent_id, agent_config_hash=agent_config_hash,
                              endpoint=endpoint, auth_header=auth_header,
                              sink=sink, scope_name=scope_name)
        emitter.emit_agent_run(
            input=inp, output=(None if err else out), latency_ms=latency_ms,
            partial=True,
            reason=(f"raised {type(err).__name__}: {err}" if err
                    else _GENERIC_PARTIAL_REASON))
        emitter.flush()

    def _guard():
        if not enforce:
            return None
        from ascore.enforce.adapter_guard import build_enforce_guard
        return build_enforce_guard(agent_id, enforce, reg=reg, cfg=cfg)

    is_async = _is_coro(fn)

    if is_async:
        @functools.wraps(fn)
        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            guard = _guard()
            if guard is not None:
                guard.begin()
            t0 = time.time_ns()
            out = None
            err: Exception | None = None
            try:
                out = await fn(*args, **kwargs)
                return out
            except Exception as e:  # behavior-identical: observe then re-raise
                err = e
                raise
            finally:
                _emit(_first_arg(args, kwargs), out, err, t0)
                if guard is not None:
                    guard.end()
        return awrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        guard = _guard()
        if guard is not None:
            guard.begin()
        t0 = time.time_ns()
        out = None
        err = None
        try:
            out = fn(*args, **kwargs)
            return out
        except Exception as e:  # behavior-identical: observe then re-raise
            err = e
            raise
        finally:
            _emit(_first_arg(args, kwargs), out, err, t0)
            if guard is not None:
                guard.end()
    return wrapper


def _generic_trace(fn: Any, **kw: Any):
    if not callable(fn):
        raise TypeError(
            "agenttic.trace: don't know how to trace a "
            f"{type(fn).__name__!r}; pass a LangGraph graph, an OpenAI Agents "
            "agent, or a callable (query -> response).")
    return _wrap_callable(fn, **kw)


def _is_coro(fn: Any) -> bool:
    import asyncio
    if asyncio.iscoroutinefunction(fn):
        return True
    call = getattr(fn, "__call__", None)
    return call is not None and asyncio.iscoroutinefunction(call)


def _first_arg(args: tuple, kwargs: dict) -> Any:
    """The agent's query: the first positional arg, else the first kwarg."""
    if args:
        return args[0]
    if kwargs:
        return next(iter(kwargs.values()))
    return None
