# LlamaIndex → Agenttic (zero-touch OTel)

If your LlamaIndex app already emits OpenTelemetry GenAI spans (e.g. via an
OpenInference instrumentor or the OTel SDK), point the exporter at Agenttic.

## Config (copy-paste)

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="https://your-agenttic/v1/traces"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"
export OTEL_SERVICE_NAME="llamaindex-agent"
# export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer <token>"
```

In-code exporter setup: see [generic-otlp.md](generic-otlp.md).

## Captured vs not

| Captured | Not captured |
|----------|--------------|
| LLM spans → `llm_call` (model, tokens) | Index/embedding internals not emitted as GenAI spans |
| Tool / query-engine tool spans → `tool_call` | Retrieval spans map to steps only when they carry GenAI/retrieval attributes; raw docs are hashed, not stored |
| Trace grouping per query, timing, errors | A certification tier — live LlamaIndex spans are NOT ASSESSED (monitoring only) |

Ingested traces are `source=otel_ingest`, excluded from batch scorecards. Verify:

```bash
agenttic doctor --target https://your-agenttic/v1/traces
```
