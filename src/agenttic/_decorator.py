"""@instrument decorator + session() context manager (SPEC-8 Step 42).

For custom / homegrown agents that aren't a supported framework: wrap any
``query -> response`` function (or an arbitrary block of code) so it emits a
canonical Agenttic run — input, output, timing.

    from agenttic import instrument, session

    @instrument                       # or @instrument(agent_id="my-agent")
    def my_agent(query: str) -> str:
        ...

    with session(agent_id="my-agent") as run:   # for code that isn't one fn
        run.input = query
        run.output = do_work(query)

Honest degradation (Hard Rule 39): when the tool trajectory can't be observed
(the general case for an opaque callable), the run is marked ``partial`` with a
logged reason — Agenttic never invents tool calls. Same target/enforce and
no-silent-phone-home semantics as :func:`agenttic.trace` (Hard Rule 38): with no
target configured, wrapping runs the code unchanged and emits nothing, logging
where to configure a target.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ascore.ingest.emit import SpanEmitter

# The generic-run mechanism is shared with the auto-detecting trace() fallback:
# @instrument and trace(plain_callable) produce the same canonical run.
from ._detect import (
    _GENERIC_PARTIAL_REASON,
    _guidance_once,
    _resolve_enforce,
    _resolve_target,
    _wrap_callable,
)

log = logging.getLogger("agenttic")

DEFAULT_AGENT_ID = "instrumented-agent"
_SCOPE = "agenttic_instrument"


def instrument(fn: Any = None, *, agent_id: str = DEFAULT_AGENT_ID,
               target: str | None = None, enforce: Any = False,
               agent_config_hash: str = "", sink: list | None = None,
               reg: Any = None, cfg: Any = None):
    """Decorate a ``query -> response`` function so each call emits a canonical
    run. Usable bare (``@instrument``) or parameterized
    (``@instrument(agent_id=...)``). Sync and async functions are both
    supported; the wrapper is behavior-identical (returns byte-identical output,
    re-raises the same exception)."""
    def decorate(func: Any):
        endpoint = _resolve_target(target, cfg)
        posture = _resolve_enforce(enforce, cfg)
        if endpoint is None and sink is None:
            _guidance_once()
        return _wrap_callable(
            func, agent_id=agent_id, agent_config_hash=agent_config_hash,
            endpoint=endpoint, auth_header=None, sink=sink, enforce=posture,
            reg=reg, cfg=cfg, scope_name=_SCOPE)

    if fn is not None:            # @instrument
        return decorate(fn)
    return decorate               # @instrument(...) / instrument(...)(fn)


class _Session:
    """The object yielded by :func:`session`. Set ``.input`` and ``.output``;
    on exit, one canonical (partial) run is emitted."""

    def __init__(self, *, agent_id: str, agent_config_hash: str,
                 endpoint: str | None, auth_header: str | None,
                 sink: list | None, enforce: Any, reg: Any, cfg: Any,
                 input: Any = None):
        self.agent_id = agent_id
        self.input = input
        self.output: Any = None
        self._agent_config_hash = agent_config_hash
        self._endpoint = endpoint
        self._auth_header = auth_header
        self._sink = sink
        self._enforce = enforce
        self._reg = reg
        self._cfg = cfg
        self._t0: int | None = None
        self._guard = None

    def __enter__(self) -> "_Session":
        self._t0 = time.time_ns()
        if self._enforce:
            from ascore.enforce.adapter_guard import build_enforce_guard
            self._guard = build_enforce_guard(
                self.agent_id, self._enforce, reg=self._reg, cfg=self._cfg)
            self._guard.begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        latency_ms = (time.time_ns() - (self._t0 or time.time_ns())) / 1e6
        emitter = SpanEmitter(
            self.agent_id, agent_config_hash=self._agent_config_hash,
            endpoint=self._endpoint, auth_header=self._auth_header,
            sink=self._sink, scope_name=_SCOPE)
        emitter.emit_agent_run(
            input=self.input,
            output=(None if exc_type else self.output),
            latency_ms=latency_ms, partial=True,
            reason=(f"raised {exc_type.__name__}: {exc}" if exc_type
                    else _GENERIC_PARTIAL_REASON))
        emitter.flush()
        if self._guard is not None:
            self._guard.end()
        return False  # never suppress the caller's exception (behavior-identical)


def session(*, agent_id: str = DEFAULT_AGENT_ID, target: str | None = None,
            enforce: Any = False, agent_config_hash: str = "",
            sink: list | None = None, reg: Any = None, cfg: Any = None,
            input: Any = None) -> _Session:
    """Context-manager form of :func:`instrument` for code that isn't a single
    function. Produces the same canonical run as the decorator."""
    endpoint = _resolve_target(target, cfg)
    posture = _resolve_enforce(enforce, cfg)
    if endpoint is None and sink is None:
        _guidance_once()
    return _Session(
        agent_id=agent_id, agent_config_hash=agent_config_hash,
        endpoint=endpoint, auth_header=None, sink=sink, enforce=posture,
        reg=reg, cfg=cfg, input=input)
