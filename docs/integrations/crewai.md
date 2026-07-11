# CrewAI → Agenttic (zero-touch OTel)

If your CrewAI app is already instrumented with OpenTelemetry GenAI spans (e.g.
via an OpenInference / OpenLLMetry instrumentor, or the OTel SDK directly), point
the exporter at Agenttic — no code change.

## Config (copy-paste)

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="https://your-agenttic/v1/traces"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"
export OTEL_SERVICE_NAME="crewai-crew"
# export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer <token>"
```

That's it — your crew's LLM and tool spans flow to Agenttic. See
[generic-otlp.md](generic-otlp.md) for the in-code exporter setup if you wire the
SDK yourself.

## Captured vs not

| Captured | Not captured |
|----------|--------------|
| Agent/LLM spans → `llm_call` (model, tokens) | Crew orchestration steps that aren't emitted as GenAI spans |
| Tool spans → `tool_call` (tool name) | Raw task inputs/outputs (content hashes only unless opted in) |
| Per-run trace grouping, timing, errors | A certification tier — CrewAI live spans are NOT ASSESSED (monitoring/drift only) |

Ingested traces are `source=otel_ingest`, excluded from batch scorecards. Verify:

```bash
agenttic doctor --target https://your-agenttic/v1/traces
```
