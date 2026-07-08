"""OTel-GenAI ingest (SPEC-7 Step 35).

Agenttic as a *peer on the customer's existing OTel bus*: accept spans following
the OpenTelemetry GenAI semantic conventions — via an OTLP/HTTP endpoint or a
batch file importer — and map them into Agenttic ``Trace`` (and, where a span
describes a gateway decision, ``Decision``) objects.

Hard rules (SPEC-7 31, 33): ingest **observes**, it never blocks; and it speaks
the maintained OTel wire format — it never invents one. Live-ingested traces are
recorded with provenance ``source="otel_ingest"`` and stored as ``mode="live"``,
so they can never mix into batch certification scorecards (SPEC-1 Step 9
invariant).
"""

from ascore.ingest.otel import (
    OtelSpan,
    load_span_dump,
    otlp_success_response,
    parse_otlp,
)

__all__ = [
    "OtelSpan",
    "parse_otlp",
    "load_span_dump",
    "otlp_success_response",
]
