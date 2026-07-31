"""OTel-GenAI ingest (SPEC-7 Step 35).

Agenttic as a *peer on the customer's existing OTel bus*: accept spans following
the OpenTelemetry GenAI semantic conventions — via an OTLP/HTTP endpoint or a
batch file importer — and map them into Agenttic ``Trace`` (and, where a span
describes a gateway decision, ``Decision``) objects.

Hard rules (SPEC-7 31, 33): ingest **observes**, it never blocks; and it speaks
the maintained OTel wire format — it never invents one. Live-ingested traces are
recorded with provenance ``source="otel_ingest"`` and stored as ``mode="live"``,
so they can never mix into batch certification scorecards (SPEC-1 Step 9
invariant). Ingest hashes content instead of storing it, so a trace can arrive
with no recoverable answer text: its ``final_output`` then carries the
:data:`~agenttic.ingest.mapping.NO_OUTPUT_CAPTURED` marker (re-exported here)
rather than a digest a consumer would read as the agent's reply. The same rule
applies to a failure the producer declared without describing: it arrives as the
:data:`~agenttic.ingest.mapping.ERROR_NO_MESSAGE` marker plus the normalised
``otel.status_code`` attribute, never as a silently clean span.
"""

from agenttic.ingest.mapping import (
    ERROR_NO_MESSAGE,
    NO_OUTPUT_CAPTURED,
    OTEL_STATUS_ERROR,
    ingest_otlp_payload,
    ingest_spans,
    map_decision,
    map_span,
    spans_to_traces,
    status_is_error,
)
from agenttic.ingest.otel import (
    OtelSpan,
    load_span_dump,
    otlp_success_response,
    parse_otlp,
)

__all__ = [
    # a consumer that must tell "no output was captured" from "the output was
    # this text" needs this marker; it should not have to reach into .mapping
    "NO_OUTPUT_CAPTURED",
    # …and one that must tell "the producer declared this failed, wordlessly"
    # from "the tool reported this failure" needs these. `status_is_error` is
    # exported so nobody writes a second, subtly different version of the test.
    "ERROR_NO_MESSAGE",
    "OTEL_STATUS_ERROR",
    "status_is_error",
    "OtelSpan",
    "parse_otlp",
    "load_span_dump",
    "otlp_success_response",
    "map_span",
    "map_decision",
    "spans_to_traces",
    "ingest_spans",
    "ingest_otlp_payload",
]
