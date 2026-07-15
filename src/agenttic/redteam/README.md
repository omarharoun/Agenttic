# `agenttic.redteam` — the adversarial test-case generator (sparring partner)

Reads a target agent's **declared interface** — tool schema, system prompt, and
declared secrets — and emits scoreable attack
[`TestCase`](../schema/testcase.py) objects, each with a **filled deterministic
oracle** so a failure is *checkable, not vibes*. It then runs the probes through
the **existing** adapter + scorer, keeps the ones that break the agent, mutates
around the winners, and promotes them into a versioned regression suite via the
**existing** hardening path. Nothing here signs certificates or touches the stats
core; the scorer/adapter/hardening are reused, not rebuilt.

## The flow

```
descriptor ─▶ GENERATE ─▶ RUN ─▶ KEEP ─▶ MUTATE ─▶ PROMOTE
 (interface)   (author)  (adapter (unsafe  (round-1  (hardening
               probes)   +scorer) only)   neighbours) regression suite)
```

1. **GENERATE** — an *author* writes probes from the agent's **real** tools /
   prompt / secret (`authors.py`). Each probe names THIS agent's real tool (e.g.
   `lookup_kb`), targets its real declared secret, and carries a filled oracle:
   - `injection_target` (+ `forbidden_tools`) — hijack into a real tool.
   - `secret` — exfiltration of a real declared secret string.
   - `forbidden_tools` / `danger_markers` — dangerous-arg / tool-misuse.
   Only probes with a deterministic, checkable oracle are emitted.
2. **RUN** — each probe is executed through the existing `AgentAdapter` and
   scored by the existing `scoring.engine.score_run` against `attack_rubric()`
   (three existing deterministic safety checks).
3. **KEEP** — retain only probes scored **unsafe** (`passed == False`); discard
   survivors.
4. **MUTATE** — one round of neighbours around each winner (same kind + same
   oracle), re-run, keep new winners (`generator.mutate_spec`).
5. **PROMOTE** — persist winners and promote them into
   `regress--<agent>--<suite>` via `hardening.promote_failures_op`. Only probes
   with a real, checkable oracle outcome are promoted — no fabricated ground
   truth.

## Run it

```bash
# CLI (deterministic author, no API key/credits needed):
uv run agenttic generate --target reference            # author + run + keep + mutate
uv run agenttic generate --target reference --promote  # ...and harden the winners

# Standalone example (same flow):
uv run python examples/attack_generator_demo.py
```

Example output: 9 of 12 round-1 probes break the reference stand-in (the 3
`direct` probes are caught by its shallow guard and survive); 18 mutation
neighbours also break it; 27 winners are promoted into a versioned regression
suite.

## Where the real LLM red-teamer plugs in

The author is a **pluggable strategy** (`authors.Author`). The default,
`TemplateAuthor`, is deterministic so the whole pipeline runs offline. The live
LLM red-teamer is `LLMRedTeamAuthor` — **this is the plug-in point**:

- It builds the red-team prompt from the agent's *actual* tools/prompt/secret
  (`LLMRedTeamAuthor.build_prompt`).
- It is **fenced**: it reuses the platform's judge/server key path (pass a
  `client` + `model`) and, when no credentials are available (e.g. the Anthropic
  account is out of credits), it raises `NoRedTeamModel` instead of hard-failing.
  `AttackGenerator.generate` catches that and **falls back** to the deterministic
  author, so an end-to-end run never requires a key.
- When wiring a live model, parse its output into `AttackSpec(...)` with a real
  filled oracle and **drop any probe whose oracle isn't deterministically
  checkable** — never fabricate ground truth.

## Files

| file | role |
|------|------|
| `descriptor.py` | `AgentDescriptor` (tools + prompt + secrets); `reference_descriptor()` reads the real reference-agent schema |
| `authors.py` | `TemplateAuthor` (deterministic default) and `LLMRedTeamAuthor` (live plug-in point) |
| `probe.py` | `AttackSpec`, `attack_rubric()`, `build_test_case()` |
| `demo_target.py` | no-key stand-in target: the real reference adapter + a scripted vulnerable client |
| `generator.py` | `AttackGenerator` — generate / run+filter / mutate / promote; `run_generation()` convenience |

## Honesty notes

- The reference agent is a benign calculator/KB DUT with no dangerous tools, so a
  fully-real LLM run of it (which needs a key) would break on nothing. The no-key
  example therefore points the generator at a **deterministic stand-in target**
  (`demo_target.py`): the *real* reference adapter driven by a *scripted* client
  that models a plausibly-vulnerable agent (a shallow keyword denylist bypassed
  by obfuscation). Only the model is stand-in — the adapter, scorer, oracles and
  hardening are the real ones. Pointed at a live agent (`build_adapter(...)` +
  key), the same probes run unchanged.
- The reference descriptor declares a **demo secret** (`internal_api_token`) so
  the exfiltration oracle has a concrete, checkable target; it is not a real
  credential. A real target declares its own secrets.
