# LangGraph / LangChain → Agenttic (zero-touch OTel)

Two ways in:

1. **Zero-touch OTel** (this page) — if your LangGraph/LangChain app already
   emits OpenTelemetry GenAI spans, point the exporter at Agenttic.
2. **One line with the adapter** — `pip install 'agenttic[langgraph]'` then
   `from agenttic import trace; graph = trace(compiled_graph)` (see
   [../QUICKSTART.md](../QUICKSTART.md)). Use this if you're not emitting OTel
   yet.

## Config (copy-paste — zero-touch)

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="https://your-agenttic/v1/traces"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"
export OTEL_SERVICE_NAME="langgraph-agent"
# export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer <token>"
```

Any LangChain OTel instrumentation that emits `gen_ai.*` spans (chat model + tool
runs) will be understood. For the in-code SDK exporter, see
[generic-otlp.md](generic-otlp.md).

## Captured vs not

| Captured | Not captured |
|----------|--------------|
| Chat-model spans → `llm_call` (model, tokens) | Graph edges/state transitions not emitted as GenAI spans |
| Tool spans → `tool_call` (tool name) | Raw messages/tool args (content hashes only unless opted in) |
| Trace grouping per graph run, timing, errors | A certification tier — live LangGraph spans are NOT ASSESSED (monitoring only) |

Ingested traces are `source=otel_ingest`, excluded from batch scorecards. Verify:

```bash
agenttic doctor --target https://your-agenttic/v1/traces
```
