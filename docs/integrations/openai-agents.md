# OpenAI Agents SDK → Agenttic (zero-touch OTel)

Two ways in:

1. **Zero-touch OTel** (this page) — if your OpenAI Agents app already emits
   OpenTelemetry GenAI spans, point the exporter at Agenttic.
2. **One line with the adapter** — `pip install 'agenttic[openai]'` then
   `from agenttic import trace; agent = trace(my_agent)` (see
   [../QUICKSTART.md](../QUICKSTART.md)).

## Config (copy-paste — zero-touch)

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="https://your-agenttic/v1/traces"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"
export OTEL_SERVICE_NAME="openai-agent"
# export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer <token>"
```

Any OTel instrumentation emitting `gen_ai.*` spans for the run (model calls +
tool calls) is understood. In-code exporter setup: [generic-otlp.md](generic-otlp.md).

## Captured vs not

| Captured | Not captured |
|----------|--------------|
| Model spans → `llm_call` (model, tokens) | Handoffs / guardrail steps not emitted as GenAI spans |
| Function/tool spans → `tool_call` (tool name) | Raw inputs/outputs (content hashes only unless opted in) |
| Trace grouping per run, timing, errors | A certification tier — live spans are NOT ASSESSED (monitoring only) |

Ingested traces are `source=otel_ingest`, excluded from batch scorecards. Verify:

```bash
agenttic doctor --target https://your-agenttic/v1/traces
```
