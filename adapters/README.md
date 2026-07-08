# Agenttic framework adapters — authoring guide

A framework adapter is a **thin** package that lets a team put their existing
agent onto the Agenttic OTel bus in two lines:

```python
from agenttic_langgraph import trace
graph = trace(graph, agent_id="support-bot", endpoint="https://agenttic.internal")
```

From then on every run emits OpenTelemetry **GenAI** spans that Agenttic ingests
(`POST /v1/traces`) into `Trace` objects — the same front door documented in
[`docs/OTEL_INTEROP.md`](../docs/OTEL_INTEROP.md). The two reference adapters,
[`langgraph/`](langgraph/) and [`openai_agents/`](openai_agents/), are the
templates. This guide is the contract a **third** adapter is written against.

## The contract (five rules)

1. **Observe, never block (Hard Rule 31).** An adapter emits spans. It must not
   change what the agent does or returns. The reference adapters guarantee this
   by hooking *observational* callbacks and by wrapping the agent transparently
   (`__getattr__` delegates everything else). A `trace(agent)`-wrapped agent must
   produce byte-identical outputs to the unwrapped one.

2. **Public API only (Hard Rule 32).** Hook the framework's *documented*
   extension point — a callback handler, a middleware, a lifecycle-hook object.
   Never import a private module (any dotted path with a `_`-prefixed segment),
   never monkey-patch framework classes, never reach into internals. If the
   framework has no public hook for something, say so in the adapter README
   rather than working around it.

3. **Speak OTel-GenAI, invent nothing (Hard Rule 33).** Build spans with
   `ascore.ingest.emit.SpanEmitter`, which emits the GenAI semantic conventions
   as OTLP/JSON. Don't hand-roll a wire format. Add LLM calls with
   `emit_llm_call(...)` and tool calls with `emit_tool_call(...)`; the emitter
   handles the OTLP envelope and the best-effort, non-blocking flush.

4. **Fail loud on enforcement, default to observe.** `trace()` takes an optional
   `enforce=`. Route it through `ascore.enforce.adapter_guard.build_enforce_guard`,
   which validates a compiled policy exists (raising `EnforceConfigError`
   otherwise) and runs at the **non-blocking** shadow posture. Inline blocking
   postures are reached only through the Step 39 ramp — never the adapter.

5. **Degrade honestly.** Extract fields defensively (frameworks vary across
   versions). A missing token count or model name is left unset, never invented.

## Anatomy of an adapter

```
adapters/<framework>/
├── pyproject.toml                 # name: agenttic-<framework>; deps: ascore + the SDK
└── agenttic_<framework>/
    └── __init__.py                # exports trace(); a public-hook handler; a wrapper
```

`__init__.py` has three parts, mirrored in both reference adapters:

- **A public-hook handler** (`AgentticCallbackHandler` / `AgentticRunHooks`) that
  turns framework events into `SpanEmitter.emit_*` calls. Base-class it on the
  framework's public hook type when importable, else `object` (so the package
  imports even where the SDK is absent).
- **A transparent wrapper** (`_TracedGraph` / `_TracedAgent`) that injects the
  handler on the run entrypoint and delegates every other attribute to the
  wrapped object.
- **`trace(...)`** — the two-line entrypoint, wiring `agent_id`,
  `agent_config_hash`, `endpoint`/`auth_header` (or a `sink` for tests/air-gap),
  and the optional `enforce=` guard.

## Writing the third adapter

1. Find the framework's **public** observability hook (callback/middleware/
   tracer). Confirm it's documented and stable.
2. Copy a reference `__init__.py`; replace the handler base and the event
   methods with the framework's; map each event to `emit_llm_call` /
   `emit_tool_call`.
3. Make the wrapper inject the handler on the framework's run call and delegate
   otherwise.
4. Ship a golden end-to-end test: drive the handler through the framework's
   public test utilities (or a fake that mimics the public contract), flush to a
   `sink`, feed the payload to `ascore.ingest.ingest_otlp_payload`, and assert
   the resulting `Trace` has the expected spans + I/O hashes — and that wrapped
   vs unwrapped outputs are identical.
5. If a capability has no public hook, document the gap here. That honesty is
   the point: a coverage gap named is better than an internal reached into.

## What lives where

| Concern | Location |
|---|---|
| OTLP-GenAI span building + flush | `ascore.ingest.emit.SpanEmitter` (shared) |
| Span → Trace/Decision ingest | `ascore.ingest` (`/v1/traces`, `ascore ingest otel`) |
| enforce= policy guard | `ascore.enforce.adapter_guard` (shared) |
| Framework-specific hook wiring | the adapter package (thin) |

Keep the adapter thin: everything reusable already lives in `ascore`.
