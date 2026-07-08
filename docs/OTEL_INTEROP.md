# OpenTelemetry interop — the round trip

Agenttic is a **peer on your existing OTel bus**, not a replacement pane of
glass. It speaks the OpenTelemetry **GenAI semantic conventions** in both
directions, so agent telemetry you already emit becomes certification evidence
without a parallel pipeline. Agenttic never invents a wire format (SPEC-7 Hard
Rule 33).

## The two directions

```
                 emit (adapters / SDK exporter)
   your agent  ───────────────────────────────►  OTLP /v1/traces  ──►  Trace
      ▲                                                                   │
      │            export  (GET /api/enforce/export?fmt=otel)            │
      └──────────────  Decision / Trace as OTel spans  ◄─────────────────┘
```

- **Ingest (in).** `POST /v1/traces` accepts an OTLP/HTTP `ExportTraceService
  Request` (JSON encoding). Spans following the GenAI conventions map to
  `Trace` objects — tools and I/O hashes populated, `agent_config_hash`
  preserved, provenance `source="otel_ingest"`. A batch dump imports via
  `ascore ingest otel <file>`. See `src/ascore/ingest/`.
- **Export (out).** Enforcement decisions and traces serialize back to OTel
  spans (`GET /api/enforce/export?fmt=otel`; `src/ascore/enforce/export.py`), so
  Agenttic's own signals flow onto the same Datadog/Grafana/collector bus you
  already run.

Because both sides use the same conventions, the loop closes: an agent traced by
an adapter emits spans → Agenttic ingests them → any decision Agenttic makes can
be exported back as spans alongside the originals.

## GenAI attributes Agenttic reads on ingest

| Attribute | Maps to |
|---|---|
| `gen_ai.system`, `gen_ai.request.model` | LLM-call span identity |
| `gen_ai.usage.input_tokens` / `output_tokens` | `Span.tokens_in` / `tokens_out` |
| `gen_ai.operation.name` = `execute_tool`, `gen_ai.tool.name` | tool-call span |
| `gen_ai.tool.call` / `gen_ai.tool.message` events | tool I/O → sha256 hashes |
| `agenttic.agent_id`, `agenttic.agent_config_hash` (resource) | agent identity, preserved |
| `enforcement.*` (from Agenttic export) | `Decision` objects |

Spans missing GenAI attributes **degrade gracefully**: a partial `Span` is kept
and flagged `incomplete_span`; nothing is fabricated, nothing crashes.

## Wiring your telemetry in

**Framework adapters (two lines).** If your agent is built on LangGraph or the
OpenAI Agents SDK, use the adapters in [`adapters/`](../adapters/README.md):

```python
from agenttic_langgraph import trace
graph = trace(graph, agent_id="support-bot", endpoint="https://agenttic.internal")
```

**OTel collector.** Point an `otlphttp` exporter (JSON encoding) at
`https://agenttic.internal/v1/traces` with your API token as the `Authorization`
header. Any GenAI-instrumented service then lands in Agenttic with no code
change.

## The scorecard-exclusion invariant

Ingested traces are live/production signals. They are stored as `mode="live"`
and are **structurally excluded** from batch certification scorecards (SPEC-1
Step 9): the scorecard path reads only `mode="batch"` traces. Live telemetry
enriches monitoring and enforcement; it never quietly inflates a certification
grade. This invariant is regression-tested (`tests/test_ingest_otel.py`).

## Offline / air-gapped

Ingest and export are stdlib-only and make no outbound calls of their own, so
they run unchanged in the air-gapped deployment (see
[`docs/AIRGAP.md`](AIRGAP.md)). Only features that inherently need the internet
(hosted public verify pages) are flagged unavailable offline — never silently
degraded.
