"""OpenTelemetry tracing — optional, no-op unless enabled and installed.

When ``observability.otel_enabled`` is true and the ``otel`` extra is installed,
``setup_tracing`` wires an OTLP exporter (endpoint from
``OTEL_EXPORTER_OTLP_ENDPOINT``). ``span(name, **attrs)`` is a context manager
used to build the request → run → llm-call hierarchy; it is a cheap no-op when
tracing is off, so call sites need no conditionals and nothing extra is required
in the default deployment.
"""

from __future__ import annotations

from contextlib import contextmanager

_TRACER = None  # set by setup_tracing() when enabled+available


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
    _TRACER = trace.get_tracer("ascore")
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
