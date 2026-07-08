"""OTLP/HTTP GenAI span parsing (SPEC-7 Step 35, T35.1).

This module handles only the **wire format**: decoding an OTLP `ExportTrace
ServiceRequest` (the payload an OTel collector POSTs to ``/v1/traces``, or a
`ascore ... export`-style span dump) into a flat list of normalized
:class:`OtelSpan` records. It invents nothing — the shapes here are the maintained
OTLP/JSON encoding (proto3 JSON mapping). Turning those spans into Agenttic
``Trace``/``Decision`` objects is :mod:`ascore.ingest.mapping`.

We deliberately do not depend on the OpenTelemetry SDK to *parse* an incoming
payload (the SDK is an exporter, not a JSON schema): the OTLP/JSON encoding is a
stable, documented mapping and parsing it directly keeps ingest dependency-free
and air-gap-safe. The SDK is used on the *emit* side (adapters/export).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# OTLP span.kind enum (proto) → readable name. Not all map to GenAI semantics;
# the GenAI mapping happens in mapping.py from attributes, not from this.
_SPAN_KIND = {
    0: "unspecified", 1: "internal", 2: "server",
    3: "client", 4: "producer", 5: "consumer",
}


@dataclass
class OtelSpan:
    """A single OTel span, normalized. Resource- and scope-level attributes are
    flattened onto the span (they apply to it) but kept discoverable separately."""

    trace_id: str
    span_id: str
    name: str
    kind: str = "internal"
    parent_id: str | None = None
    start_ns: int = 0
    end_ns: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)  # {name, time_ns, attributes}
    status: dict = field(default_factory=dict)
    resource_attributes: dict[str, Any] = field(default_factory=dict)
    scope: dict = field(default_factory=dict)

    def start_time(self) -> datetime:
        return _ns_to_dt(self.start_ns)

    def end_time(self) -> datetime:
        # never before start (Span validator enforces this downstream)
        return _ns_to_dt(max(self.end_ns, self.start_ns))


def _ns_to_dt(ns: int) -> datetime:
    try:
        return datetime.fromtimestamp(int(ns) / 1e9, tz=timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def decode_anyvalue(v: Any) -> Any:
    """Decode an OTLP ``AnyValue`` into a plain Python value.

    OTLP wraps every attribute value in a one-of: ``stringValue`` / ``boolValue``
    / ``intValue`` (a string per proto3 JSON) / ``doubleValue`` / ``arrayValue`` /
    ``kvlistValue`` / ``bytesValue``. A bare (already-decoded) value passes
    through unchanged, so this is safe on both raw and pre-normalized payloads."""
    if not isinstance(v, dict):
        return v
    if "stringValue" in v:
        return v["stringValue"]
    if "boolValue" in v:
        return bool(v["boolValue"])
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (ValueError, TypeError):
            return v["intValue"]
    if "doubleValue" in v:
        try:
            return float(v["doubleValue"])
        except (ValueError, TypeError):
            return v["doubleValue"]
    if "bytesValue" in v:
        return v["bytesValue"]
    if "arrayValue" in v:
        vals = (v["arrayValue"] or {}).get("values", []) or []
        return [decode_anyvalue(x) for x in vals]
    if "kvlistValue" in v:
        return kvlist_to_dict((v["kvlistValue"] or {}).get("values", []) or [])
    return v


def kvlist_to_dict(attr_list: Any) -> dict[str, Any]:
    """Turn an OTLP KeyValue list (``[{"key":..., "value": AnyValue}]``) into a
    dict. Tolerates an already-decoded plain dict."""
    if isinstance(attr_list, dict):
        return {k: decode_anyvalue(val) for k, val in attr_list.items()}
    out: dict[str, Any] = {}
    for kv in attr_list or []:
        if not isinstance(kv, dict) or "key" not in kv:
            continue
        out[kv["key"]] = decode_anyvalue(kv.get("value"))
    return out


def _parse_event(ev: dict) -> dict:
    return {
        "name": ev.get("name", ""),
        "time_ns": _to_int(ev.get("timeUnixNano")),
        "attributes": kvlist_to_dict(ev.get("attributes", [])),
    }


def _to_int(x: Any) -> int:
    try:
        return int(x)
    except (ValueError, TypeError):
        return 0


def parse_otlp(payload: dict) -> list[OtelSpan]:
    """Parse an OTLP ``ExportTraceServiceRequest`` into normalized spans.

    Accepts the standard ``{"resourceSpans": [...]}`` envelope (collector wire
    format). Also tolerates a bare ``{"spans": [...]}`` or a top-level list, so a
    hand-exported dump still ingests. Malformed sub-objects are skipped, never
    raised — ingest observes; it does not crash the producer."""
    if isinstance(payload, list):
        payload = {"resourceSpans": payload}
    if not isinstance(payload, dict):
        return []

    spans: list[OtelSpan] = []

    resource_spans = payload.get("resourceSpans")
    if resource_spans is None and "spans" in payload:
        # bare {"spans": [...]} with optional resource/scope
        resource_spans = [{
            "resource": payload.get("resource", {}),
            "scopeSpans": [{"scope": payload.get("scope", {}),
                            "spans": payload.get("spans", [])}],
        }]

    for rs in resource_spans or []:
        if not isinstance(rs, dict):
            continue
        res_attrs = kvlist_to_dict((rs.get("resource") or {}).get("attributes", []))
        # OTLP renamed scopeSpans (was instrumentationLibrarySpans); accept both.
        scope_spans = rs.get("scopeSpans") or rs.get("instrumentationLibrarySpans") or []
        for ss in scope_spans:
            if not isinstance(ss, dict):
                continue
            scope = ss.get("scope") or ss.get("instrumentationLibrary") or {}
            for sp in ss.get("spans", []) or []:
                parsed = _parse_span(sp, res_attrs, scope)
                if parsed is not None:
                    spans.append(parsed)
    return spans


def _parse_span(sp: dict, res_attrs: dict, scope: dict) -> OtelSpan | None:
    if not isinstance(sp, dict) or not sp.get("spanId"):
        return None
    parent = sp.get("parentSpanId") or None
    if parent == "":
        parent = None
    kind = sp.get("kind", 0)
    kind_name = _SPAN_KIND.get(kind, "internal") if isinstance(kind, int) else "internal"
    return OtelSpan(
        trace_id=sp.get("traceId", ""),
        span_id=sp["spanId"],
        name=sp.get("name", ""),
        kind=kind_name,
        parent_id=parent,
        start_ns=_to_int(sp.get("startTimeUnixNano")),
        end_ns=_to_int(sp.get("endTimeUnixNano")),
        attributes=kvlist_to_dict(sp.get("attributes", [])),
        events=[_parse_event(e) for e in sp.get("events", []) or []],
        status=sp.get("status", {}) or {},
        resource_attributes=res_attrs,
        scope=scope if isinstance(scope, dict) else {},
    )


def load_span_dump(path: str | Path) -> list[OtelSpan]:
    """Load and parse an OTLP span dump from a JSON file (batch importer)."""
    data = json.loads(Path(path).read_text())
    return parse_otlp(data)


def otlp_success_response(rejected: int = 0, error: str = "") -> dict:
    """The OTLP ``ExportTraceServiceResponse`` body.

    An empty body means full success. When some spans could not be mapped we
    report them via ``partialSuccess`` exactly as the OTLP contract specifies —
    the collector treats a 200 + partialSuccess as accepted-with-warnings and
    does not retry, which is what we want (ingest never fails the producer)."""
    if rejected:
        return {"partialSuccess": {
            "rejectedSpans": str(rejected),
            "errorMessage": error or f"{rejected} span(s) could not be mapped",
        }}
    return {"partialSuccess": {}}
