"""agenttic — the public, supported entry point to the Agenttic platform.

This umbrella package is a **semver'd promise** (SPEC-8 Hard Rule 36): every
name it exports is a stable, supported surface. The implementation lives in the
internal ``agenttic.*`` package, which is not part of the public contract and may
change without notice. Import from here, never from ``ascore``.

The whole point of Agenttic distribution: a developer who has never seen it can

    pip install agenttic

add one line

    from agenttic import trace
    agent = trace(my_agent)          # wraps LangGraph / OpenAI Agents / anything

or certify from the shell

    agenttic certify --mock          # → a signed safety grade, no API key

The base install pulls **no framework SDK** and imports none (Hard Rule 37):
``import agenttic`` works with zero of LangGraph/OpenAI-Agents/LangChain
present. Framework support is optional and lazy — ``agenttic[langgraph]``,
``agenttic[openai]``, ``agenttic[all]`` — and is imported only when you actually
wrap a matching object.

Public surface (exactly ``__all__``; a test fails if anything else leaks):

* :func:`trace`      — auto-detecting wrapper for any agent framework
* :func:`instrument` — decorator (and :func:`session` context manager) for
  custom/homegrown agents
* :func:`certify`    — certify an agent against a profile → evidence dossier
* :func:`verify`     — recompute a dossier's hashes offline (signed-grade check)
* :class:`Trace`, :class:`Span` — the canonical run type the pipeline consumes
"""
# NB: no ``from __future__ import annotations`` — it would bind the name
# ``annotations`` into this namespace and leak it past the public-surface test.
# Aliased private so it never leaks into the public surface (Hard Rule 36).
from typing import Any as _Any

# Version: single source of truth for the package. `agenttic` is now both the
# public umbrella and the internal engine (the former `ascore` package was
# folded in during the rename), so the version lives here directly. The
# distribution `version` in pyproject.toml is kept in lock-step (asserted by a
# test).
__version__ = "1.0.1"

# Certification surface — re-exported directly (core, no framework SDKs).
from agenttic.certification.certify import certify as certify
from agenttic.certification.dossier import verify as verify

# The canonical run type the ingest/certification pipeline consumes.
from agenttic.schema.trace import Span as Span
from agenttic.schema.trace import Trace as Trace

__all__ = [
    "trace",
    "instrument",
    "session",
    "certify",
    "verify",
    "Trace",
    "Span",
]


# ---------------------------------------------------------------------------
# The framework-facing surface (trace / instrument / session) is defined in the
# internal submodules ``agenttic._detect`` and ``agenttic._decorator``. We
# forward to them lazily so that ``import agenttic`` never pulls a framework SDK
# (Hard Rule 37) and so the umbrella imports cleanly regardless of which extras
# are present. The lazy import is per-call and cheap.
# ---------------------------------------------------------------------------
def trace(agent: _Any, *args: _Any, **kwargs: _Any) -> _Any:
    """Wrap any agent so its runs emit a canonical Agenttic run.

    Auto-detects the framework from the object's public shape and dispatches to
    the right adapter — no need to name the framework:

        from agenttic import trace
        agent = trace(compiled_langgraph_graph)     # → LangGraph adapter
        agent = trace(openai_agents_agent)          # → OpenAI Agents adapter
        agent = trace(my_plain_callable)            # → generic OTel wrapper

    Wrapping is **behavior-identical** (Hard Rule 38): the returned object
    delegates everything to the original and returns byte-identical outputs;
    it only observes. Spans go to ``target`` (falling back to the
    ``AGENTTIC_TARGET`` / ``OTEL_EXPORTER_OTLP_ENDPOINT`` env vars); with no
    target configured it is a no-op that logs where to set one — it never
    phones home. ``enforce=True`` opts into the SPEC-4 gateway at the ramp's
    non-blocking default posture. See :func:`agenttic._detect.trace`.
    """
    from ._detect import trace as _impl
    return _impl(agent, *args, **kwargs)


def instrument(*args: _Any, **kwargs: _Any) -> _Any:
    """Decorate a custom ``query -> response`` function to emit a canonical run.

        from agenttic import instrument

        @instrument
        def my_agent(query: str) -> str:
            ...

    Captures input, output and timing. If it can see into a supported LLM/tool
    client called inside, it records the tool trajectory; otherwise it marks the
    trajectory ``partial`` and logs why — it never fabricates tool calls
    (Hard Rule 39). Same target/enforce and no-phone-home semantics as
    :func:`trace`. See :func:`agenttic._decorator.instrument`.
    """
    from ._decorator import instrument as _impl
    return _impl(*args, **kwargs)


def session(*args: _Any, **kwargs: _Any) -> _Any:
    """Context-manager form of :func:`instrument` for code that isn't a single
    function::

        with agenttic.session(agent_id="my-agent") as run:
            run.input = query
            run.output = do_work(query)

    Produces the same canonical run as the decorator. See
    :func:`agenttic._decorator.session`.
    """
    from ._decorator import session as _impl
    return _impl(*args, **kwargs)
