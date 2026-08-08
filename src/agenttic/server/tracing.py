"""OpenTelemetry tracing — optional, no-op unless enabled and installed.

When ``observability.otel_enabled`` is true and the ``otel`` extra is installed,
``setup_tracing`` wires an OTLP exporter (endpoint from
``OTEL_EXPORTER_OTLP_ENDPOINT``). ``span(name, **attrs)`` is a context manager
used to build the request → run → llm-call hierarchy; it is a cheap no-op when
tracing is off, so call sites need no conditionals and nothing extra is required
in the default deployment.

``setup_langwatch`` is the same story pointed at LangWatch: with the ``langwatch``
extra installed and ``LANGWATCH_API_KEY`` set, every ``anthropic`` call in the
process is captured (prompts, completions, tokens, cost) and nested under the
spans this module already emits. It is opt-in by key, because a run's prompts and
completions are the customer's content and shipping them to a third party is a
decision, not a default.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

_TRACER = None  # set by setup_tracing()/setup_langwatch() when available


def setup_tracing(cfg: dict) -> bool:
    """Configure OTel from config; return True if tracing is active. Safe to
    call when OTel isn't installed or is disabled (returns False)."""
    global _TRACER
    obs = (cfg.get("observability", {}) or {})
    if not obs.get("otel_enabled", False):
        _TRACER = None
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter)
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _TRACER = None
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": "agenttic"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("agenttic")
    return True


def setup_langwatch() -> bool:
    """Export LLM calls and agenttic spans to LangWatch; return True if active.

    No-op unless ``LANGWATCH_API_KEY`` is set and the ``langwatch`` extra is
    installed. Call it AFTER :func:`setup_tracing`: LangWatch attaches its
    exporter to an existing global ``TracerProvider`` rather than replacing it,
    so an in-cluster OTLP collector keeps receiving the same spans.

    ``LANGWATCH_ENDPOINT`` (self-hosted instance) is read by the SDK itself.
    """
    global _TRACER
    if not os.environ.get("LANGWATCH_API_KEY"):
        return False
    try:
        import langwatch
        from openinference.instrumentation.anthropic import AnthropicInstrumentor
        from opentelemetry import trace
    except ImportError:
        return False
    # Instruments anthropic globally — every client.messages.create() in the
    # process, so no call site needs a decorator.
    langwatch.setup(instrumentors=[AnthropicInstrumentor()])
    if _TRACER is None:
        # span() was a no-op until now; light it up so run.suite / http.request
        # become the parent traces the LLM calls nest under.
        _TRACER = trace.get_tracer("agenttic")
    return True


@contextmanager
def span(name: str, **attrs):
    """Start a span (no-op when tracing is disabled)."""
    if _TRACER is None:
        yield None
        return
    with _TRACER.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v)
        yield sp
