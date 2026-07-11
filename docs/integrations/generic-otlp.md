# Generic OTLP → Agenttic

Any OpenTelemetry SDK that exports over OTLP/HTTP can send GenAI spans to
Agenttic. Point the traces exporter at Agenttic's ingest endpoint.

## Config (copy-paste)

```bash
# Send traces to Agenttic's OTLP/HTTP ingest (JSON encoding).
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="https://your-agenttic/v1/traces"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"

# Optional: authenticate + label the producing service.
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer <token>"
export OTEL_SERVICE_NAME="my-agent"
```

In code, the standard exporter needs no Agenttic-specific setup:

```python
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # reads the env above
```

Agenttic reads `agenttic.agent_id` / `agenttic.agent_config_hash` resource
attributes when present, and otherwise falls back to `service.name` for the
agent id (never fabricated).

## Captured vs not

| Captured | Not captured |
|----------|--------------|
| LLM spans → `llm_call` (model, token usage) | Raw prompts/completions (hashes only, unless opted in) |
| Tool spans → `tool_call` (tool name) | Any step not emitted as a GenAI span |
| Trace grouping, timing, error status | A certification tier — live traces are NOT ASSESSED, monitoring only |

Spans arrive **live-provenanced** and are excluded from batch scorecards. Verify:

```bash
agenttic doctor --target https://your-agenttic/v1/traces
```
