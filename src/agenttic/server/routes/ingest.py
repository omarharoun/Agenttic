"""OTel-GenAI ingest endpoint (SPEC-7 Step 35).

``POST /v1/traces`` — an OTLP/HTTP-compatible receiver. An OpenTelemetry
collector (or an SDK exporter) configured with the JSON encoding can point its
``otlphttp`` exporter straight at Agenttic, and the spans land as live
(``source="otel_ingest"``) Traces. The path and response shape are the OTLP
contract — we invent nothing (Hard Rule 33).

Ingest **observes**; it never blocks the producer. A payload with some
unmappable spans still returns HTTP 200 with an OTLP ``partialSuccess`` naming
the rejected count, exactly as the spec prescribes, so the collector does not
retry (Hard Rule 31).
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from agenttic.ingest.mapping import ingest_otlp_payload
from agenttic.ingest.otel import otlp_success_response

router = APIRouter(tags=["ingest"])


def _rejected_count(report: dict) -> int:
    return sum(1 for n in report.get("notes", [])
               if n.startswith(("skipped_span:", "empty_trace:")))


@router.post("/v1/traces")
async def otlp_traces(request: Request, response: Response):
    ctype = request.headers.get("content-type", "")
    if "protobuf" in ctype:
        # We accept the OTLP/JSON encoding. Protobuf is a valid OTLP encoding but
        # decoding it needs the opentelemetry-proto package; rather than invent a
        # parser we say so plainly (Hard Rule 33) instead of silently dropping.
        response.status_code = 415
        return {"error": "OTLP/protobuf not supported by this receiver; "
                         "configure the exporter with encoding: json"}
    try:
        payload = await request.json()
    except Exception:
        response.status_code = 400
        return {"error": "invalid JSON body"}

    report = ingest_otlp_payload(request.state.reg, payload)
    rejected = _rejected_count(report)
    # 200 in both the full- and partial-success cases (ingest never fails loud).
    return otlp_success_response(rejected=rejected)
