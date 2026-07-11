# Zero-touch OTel integration

If your stack **already emits OpenTelemetry GenAI spans**, you don't need the
Agenttic SDK at all — just point your existing OTLP exporter at Agenttic's
ingest endpoint and traces start arriving. No code change, no re-instrumentation.

```
your app → (existing OTel exporter) → https://your-agenttic/v1/traces
```

One short page per framework, each with the exact copy-paste config and an
honest statement of what is captured vs not:

| Framework | Page |
|-----------|------|
| Generic OTLP (any OpenTelemetry SDK) | [generic-otlp.md](generic-otlp.md) |
| CrewAI | [crewai.md](crewai.md) |
| LangGraph / LangChain | [langgraph.md](langgraph.md) |
| LlamaIndex | [llamaindex.md](llamaindex.md) |
| OpenAI Agents SDK | [openai-agents.md](openai-agents.md) |

## What Agenttic ingest does with your spans

Agenttic's `/v1/traces` endpoint speaks **OTLP/HTTP JSON**. It groups spans by
trace id and maps GenAI spans to a canonical Agenttic **Trace**:

- **LLM spans** (`gen_ai.operation.name = chat`, `gen_ai.request.model`, token
  usage) → `llm_call` steps.
- **Tool spans** (`gen_ai.tool.name`, `execute_tool …`) → `tool_call` steps.
- Prompts/completions/arguments are recorded as **content hashes**, never raw
  payloads, unless you explicitly opt in (Hard Rule 30).

## What it does NOT do (the NOT-ASSESSED contract)

Zero-touch ingest is **observation**, not certification:

- Ingested traces are stored **live-provenanced** (`source = otel_ingest`,
  `mode = live`) and are **structurally excluded from batch certification
  scorecards** — a live span stream never silently becomes a graded result.
- A span that carries no GenAI attributes is kept as a **partial** step and
  flagged incomplete; Agenttic **fabricates nothing**.
- Any capability domain your spans don't exercise stays **NOT ASSESSED** — the
  honest default. Live traces inform monitoring/drift, not a tier.

Confirm setup in one command once traces are flowing:

```bash
agenttic doctor --target https://your-agenttic/v1/traces
```
