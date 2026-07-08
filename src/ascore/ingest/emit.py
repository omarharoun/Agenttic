"""OTel-GenAI span emission (SPEC-7 Step 36 support).

The *emit* side of the OTel bus, shared by the framework adapters. A
:class:`SpanEmitter` builds spans following the GenAI semantic conventions and
flushes them as an OTLP/HTTP ``ExportTraceServiceRequest`` — the exact wire
format :mod:`ascore.ingest.otel` parses, so ``trace(agent) → spans → ingested
Traces`` round-trips through Agenttic's own front door.

No OpenTelemetry SDK dependency: we emit OTLP/JSON directly (stdlib only), which
keeps the adapters thin and air-gap-safe. Emission is **best-effort and
non-blocking** — a flush failure is swallowed (optionally logged), never raised
into the agent's control flow (Hard Rule 31: adapters observe, never block).
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any


class SpanEmitter:
    """Accumulate GenAI spans for one agent run and flush them as OTLP/JSON."""

    def __init__(self, agent_id: str, *, agent_config_hash: str = "",
                 endpoint: str | None = None, auth_header: str | None = None,
                 scope_name: str = "agenttic", sink: list | None = None,
                 trace_id: str | None = None):
        self.agent_id = agent_id
        self.agent_config_hash = agent_config_hash
        self.endpoint = endpoint
        self.auth_header = auth_header
        self.scope_name = scope_name
        # A sink (list) captures the OTLP payload instead of HTTP — used by tests
        # and by air-gapped/manual pipelines.
        self.sink = sink
        self.trace_id = trace_id or f"{int(time.time_ns()):032x}"[-32:]
        self._spans: list[dict] = []
        self._counter = 0

    # -- low-level ---------------------------------------------------------
    def _next_span_id(self) -> str:
        self._counter += 1
        return f"{self._counter:016x}"

    @staticmethod
    def _kv(attrs: dict[str, Any]) -> list[dict]:
        out = []
        for k, v in attrs.items():
            if v is None:
                continue
            if isinstance(v, bool):
                val = {"boolValue": v}
            elif isinstance(v, int):
                val = {"intValue": str(v)}
            elif isinstance(v, float):
                val = {"doubleValue": v}
            else:
                val = {"stringValue": v if isinstance(v, str) else json.dumps(v, default=str)}
            out.append({"key": k, "value": val})
        return out

    def _add_span(self, name: str, attributes: dict, events: list[dict],
                  *, parent_id: str | None = None, kind: int = 1) -> str:
        now = time.time_ns()
        span_id = self._next_span_id()
        self._spans.append({
            "traceId": self.trace_id,
            "spanId": span_id,
            "parentSpanId": parent_id or "",
            "name": name,
            "kind": kind,
            "startTimeUnixNano": str(now),
            "endTimeUnixNano": str(now),
            "attributes": self._kv(attributes),
            "events": events,
            "status": {"code": 1},
        })
        return span_id

    def _event(self, name: str, attributes: dict) -> dict:
        return {"name": name, "timeUnixNano": str(time.time_ns()),
                "attributes": self._kv(attributes)}

    # -- GenAI conveniences ------------------------------------------------
    def emit_llm_call(self, *, system: str = "", model: str = "",
                      prompt: str | None = None, completion: str | None = None,
                      input_tokens: int | None = None,
                      output_tokens: int | None = None,
                      parent_id: str | None = None) -> str:
        events = []
        if prompt is not None:
            events.append(self._event("gen_ai.user.message", {"content": prompt}))
        if completion is not None:
            events.append(self._event("gen_ai.assistant.message", {"content": completion}))
        return self._add_span(
            f"chat {system}".strip(),
            {"gen_ai.system": system, "gen_ai.operation.name": "chat",
             "gen_ai.request.model": model,
             "gen_ai.usage.input_tokens": input_tokens,
             "gen_ai.usage.output_tokens": output_tokens},
            events, parent_id=parent_id, kind=3)

    def emit_tool_call(self, *, tool_name: str, arguments: Any = None,
                       result: Any = None, parent_id: str | None = None) -> str:
        events = []
        if arguments is not None:
            events.append(self._event(
                "gen_ai.tool.call",
                {"arguments": arguments if isinstance(arguments, str)
                 else json.dumps(arguments, default=str)}))
        if result is not None:
            events.append(self._event(
                "gen_ai.tool.message",
                {"gen_ai.tool.message.content": result if isinstance(result, str)
                 else json.dumps(result, default=str)}))
        return self._add_span(
            f"execute_tool {tool_name}",
            {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool_name},
            events, parent_id=parent_id, kind=1)

    # -- output ------------------------------------------------------------
    def payload(self) -> dict:
        """The OTLP ExportTraceServiceRequest for the spans collected so far."""
        res_attrs = {"service.name": self.agent_id,
                     "agenttic.agent_id": self.agent_id}
        if self.agent_config_hash:
            res_attrs["agenttic.agent_config_hash"] = self.agent_config_hash
        return {"resourceSpans": [{
            "resource": {"attributes": self._kv(res_attrs)},
            "scopeSpans": [{"scope": {"name": self.scope_name, "version": "1"},
                            "spans": list(self._spans)}],
        }]}

    def flush(self) -> dict | None:
        """Emit the accumulated spans. Best-effort: never raises into the caller.

        Returns the OTLP payload that was emitted (for inspection), or None if
        there was nothing to send."""
        if not self._spans:
            return None
        payload = self.payload()
        if self.sink is not None:
            self.sink.append(payload)
        elif self.endpoint:
            self._post(payload)
        self._spans.clear()
        return payload

    def _post(self, payload: dict) -> None:
        url = self.endpoint.rstrip("/")
        if not url.endswith("/v1/traces"):
            url = url + "/v1/traces"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"})
        if self.auth_header:
            req.add_header("Authorization", self.auth_header)
        try:  # best-effort — observation must never break the agent
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass
