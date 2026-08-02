# Adding an agent to test

**You should not have to write Python to test an agent.** If you are reaching for
a new module in `src/agenttic/adapters/`, you are almost certainly on the wrong
rung of this ladder. Work down it and stop at the first one that fits.

| rung | when | code you write | evidence you get |
|---|---|---|---|
| 1. HTTP | the agent has an endpoint | none — a `Mapping` | black box: final text |
| 2. ACP | the agent speaks Agent Client Protocol | none — a config entry | glass box + sessions + cost |
| 3. CLI spec | the agent is a local binary | none — a config entry | glass box, pairing, answers |
| 4. OTel correlation | the agent exports OpenTelemetry spans | none — one attribute | glass box, whatever rung you drove it on |
| 5. a module | none of the above fit | ~400 lines | glass box, and a maintenance burden |

Rung 5 is a last resort, not the pattern. A verification platform that needs a
source change per subject can only verify its own agents.

---

## Rung 1 — HTTP

Already shipped as the "Connect your agent" flow. `connect.Mapping` places the
prompt into your request body and reads the reply from a dotted response path, so
an OpenAI-compatible endpoint works with no configuration at all.

The limit is real and worth stating: a black-box trace shows only what the agent
finally *said*. `scoring/engine.py` drops three checks for a black-box target
while seven registered checks read tool spans, four of them with no text
fallback. Rung 4 is how you fix that without changing rung 1.

## Rung 2 — ACP (preferred wherever it works)

[ACP](https://agentclientprotocol.com) is a JSON-RPC protocol agents implement so
clients can drive them. OpenHands, Claude Code, Codex and Gemini's CLIs speak it.

```yaml
agents:
  my-agent:
    driver: acp
    command: ["my-agent", "acp"]
    version: "1.2.3"
    model_env: LLM_MODEL
    api_key_env: LLM_API_KEY
```

Prefer this rung because the protocol **declares** what a bespoke event stream
leaves you guessing:

* `ToolKind` (`read` `edit` `delete` `move` `execute` …) → `action_risk` is
  classified from the agent's own statement, `explicit` rather than `inferred`.
  Sniffing tool names gave `action_risk` closure of **0.0** on a real run where
  the agent rewrote source files.
* `ToolCallStatus: failed` → a real tool failure instead of matching the word
  "error" in a payload.
* `Usage` → the agent's token spend, instead of a scorecard printing `$0.00`
  about a run that cost money.
* `StopReason: refusal` → a refusal as a fact, not a regex over prose.
* `session/new` + repeated `session/prompt` → a real conversation. This is the
  only rung that can hold one, so it is the only rung where `session_shape` is
  measured rather than declared unexercisable.

**Auth is agent-specific and is never guessed.** OpenHands 1.16.0 advertises one
method — an interactive OpenHands Cloud OAuth flow — which never completes
headlessly; `session/new` then returns *"Authentication required"* and we report
that, in the agent's own words. Set `auth_method` only when you know the agent
supports it non-interactively.

## Rung 3 — a CLI spec

For a binary that speaks nothing standard. The spec is data:

```yaml
agents:
  my-agent:
    driver: cli
    command: ["my-agent", "--json", "-t", "{task}"]
    extra_args: ["--override-with-envs"]   # applied only when a model is pinned
    env: { LLM_MODEL: "{model}" }
    api_key_env: LLM_API_KEY
    output: jsonl
    map:
      user_message: { when: {kind: MessageEvent, source: user}, span: user_turn }
      agent_message: { when: {kind: MessageEvent}, span: llm_call, text: content[].text }
      finish:  { when: {kind: ActionEvent, action.kind: FinishAction},
                 span: tool_call, id: tool_call_id, text: action.message, is_answer: true }
      tool_call:   { when: {kind: ActionEvent}, span: tool_call, id: tool_call_id }
      tool_result: { when: {kind: ObservationEvent}, pairs_with: tool_call, id: tool_call_id }
```

Rules are tried in order, so put the specific ones first. `when` keys may be
dotted (`action.kind`) to select on a nested discriminator. `is_answer` may sit
on a **tool** rule — agentic CLIs typically end by calling a `finish` tool rather
than sending a message, and an adapter reading only messages records a
*completed* task as a non-result.

What this rung cannot do, by construction: know what a tool *means*. Nothing in
a private event stream says a tool mutates state, so `action_risk` stays
`unknown` — never credited read-only.

## Rung 4 — correlate the agent's own telemetry

Any rung above, plus glass-box evidence, with no adapter change at all.

1. The harness mints a `gen_ai.conversation.id` per case and hands it to the
   agent (adapters stamp it on every span they emit).
2. Your agent, instrumented to the [OpenTelemetry GenAI
   conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/), stamps the
   same id on the spans it exports.
3. Ingest them (`agenttic ingest otel spans.json`) and join:

```bash
agenttic agents correlate --agent my-agent
```

A black-box run whose exported spans carry tool calls becomes **glass box**, and
the trajectory checks get real evidence.

**A join that finds nothing changes nothing.** Visibility is upgraded only when
spans actually arrived; every attached span is marked `observed_via=otel` so what
the harness saw stays distinguishable from what the agent said about itself.

## Checking your work

```bash
agenttic agents drivers                                    # what is declared
agenttic agents drivers --check my-agent --model <model>   # actually start it
```

`--check` runs one trivial task and prints what the agent supports, what it
disclosed, and whether it answered — the difference between a declaration and a
working integration. Pin `--model`, or the probe cannot say which model answered
and will tell you so.

## If you truly need a module

Then the agent speaks nothing standard, has no endpoint, and emits no telemetry.
Before writing one, read `adapters/acp_agent.py` and `adapters/cli_spec.py` and
be sure neither can be extended instead. And do this, which no amount of reading
source replaces:

> **Run the real binary once before you trust the adapter.** Source tells you the
> schema of what an agent emits; only a process tells you what actually comes out
> of it. One smoke run against OpenHands found that `--json` does not silence the
> terminal UI (27 of 33 lines were decoration, which the adapter was counting as
> lost events) and that the agent answers by calling `finish` rather than sending
> a message (so every completed task was being recorded as a harness failure).
> Neither was visible in the SDK's own event schema.
