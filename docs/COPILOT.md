# Agenttic Copilot

An in-app **agentic assistant**: a right-docked slide-out chat panel in the app
console (`/app/*`) that helps authenticated users understand AND operate the
platform. It answers questions grounded in the real platform (methodology, metric
catalog, certification, dossiers, passports, the `ascore` CLI) **and orchestrates
the platform on the user's behalf through tools** — where the tools are the
Agenttic API, scoped to that user's tenant, permissions, and budget. It reads
freely and **proposes** write/cost actions, which the user must **confirm** before
they run.

Built on **Claude Sonnet 4.6** (`claude-sonnet-4-6`), called **server-side** with
Agenttic's **own** Anthropic key — *not* a tenant's BYO key. (Tenants still need
their own key to run evaluations; the Copilot chat is platform-provided.)

## Architecture

```
ui/src/copilot/CopilotPanel.tsx   drawer UI (lazy): streams answer + tool activity + approval cards
ui/src/copilot/markdown.tsx       tiny dependency-free Markdown → React (safe; deep-links)
ui/src/AppShell.tsx               right-edge launcher + lazily-mounted <CopilotPanel>
ui/src/api.ts                     api.copilotStatus(); copilotChat()/copilotApprove() SSE clients

src/ascore/copilot/skill.py       agentic persona + guardrails (the "skill"), system-prompt builder
src/ascore/copilot/knowledge.md   curated, grounded platform knowledge injected each turn
src/ascore/copilot/tools.py       the tool registry = the real Agenttic API, tenant/role scoped
src/ascore/copilot/agent.py       streaming tool-use loop + confirmation gate + guards
src/ascore/copilot/service.py     server-side key resolution, config, plain-stream helpers
src/ascore/copilot/store.py       tenant-scoped session persistence (resumes the confirm gate)
src/ascore/copilot/credits.py     credits / cost accounting integration seam (stub)
src/ascore/server/routes/copilot.py  GET /status, POST /chat (SSE), POST /approve (SSE)
```

## The agent loop
`agent.py` drives the standard Anthropic tool-use cycle with Sonnet 4.6:
model → `tool_use` → execute the tool (the real, tenant-scoped API) → `tool_result`
→ model → … → final answer, capped at a sensible iteration budget. Each model turn
is **streamed** (`client.messages.stream(..., tools=…)`); text deltas stream to the
UI as they arrive and tool activity streams as structured events. State lives in a
JSON-serializable, tenant-scoped **session** so the confirmation gate can span
separate HTTP requests.

## Tools = the Agenttic API, scoped to the user
Tools run **in-process against the same `request.state` objects the HTTP routes
use** (`reg` / `certifier` / `cfg` / role), so the agent can never exceed what the
signed-in user could do themselves — same tenant, same auth, same budget (a real
run uses the tenant's own Anthropic key), same role checks. No invented endpoints.

**Read tools** (run freely, no confirmation):
`platform_status`, `list_agents`, `list_certification_profiles`,
`get_certification_profile`, `list_dossiers`, `get_dossier`, `verify_dossier`,
`get_certification_job`, `anthropic_key_status`.

**Write / cost tools** (spend budget or mutate state → confirmation required):
`start_certification` (POST /api/certify path — spends the tenant's Anthropic
budget, runs async), `revoke_certification` (irreversible, append-only). Both
re-check the `operator` role, exactly like their routes.

The set is a safe, useful subset chosen from the real API surface; adding more
tools is additive — implement `run` + (for writes) a `confirm` builder and register.

## Confirmation model (human-in-the-loop)
- A turn that requests any **write** tool PAUSES the whole turn (the API needs a
  result for every `tool_use` block): the agent persists it as `pending`, emits an
  `approval_required` event with a **confirmation card** (title, detail, cost note,
  risk), and the stream ends. The tool is **not** executed.
- The UI renders the card with **Confirm & run** / **Cancel**. `POST /api/copilot/approve`
  `{session_id, approved}` resolves it: on confirm the tool executes and the agent
  resumes; on deny the agent gets a "user declined" tool_result and adapts. A
  denied action never runs.
- **Clarifying questions** need no special protocol: the agent asks in text and
  stops; the user answers as the next message. The skill tells it to ask (and to
  look ids up with a read tool) rather than guess.

## Guardrails
- **Honesty** — the agent reports only what tools actually return; the skill
  forbids inventing results/tiers/grades/numbers and enforces platform semantics
  (NOT ASSESSED, assessed_seed vs assessed_real, none_found ≠ confirmed_none,
  provisional-judge caps, coverage honesty).
- **Untrusted everything** — user messages AND every tool result are
  injection-neutralized (`guard.neutralize_injection`), fenced as untrusted DATA
  (`guard.wrap_untrusted`), and secret-scrubbed (`guard.redact_secrets`) before
  re-entering the model. A tool result can't make the agent take an unapproved
  write or reveal the system prompt / a key. Streamed output is scrubbed too (with
  a tail holdback for secrets split across deltas).
- **Confirmation gate** on all writes (above); the credits gate is consulted
  **before** an approved write executes (a refusal becomes a tool_result, never a
  silent spend).
- **Rate limit** — dedicated per-session/IP sliding window (`copilot.rate_limit_per_minute`,
  default 20/min), independent of the global middleware, shared across chat +
  approve.
- **Tenant isolation** — tools + the session store are tenant-scoped; the Copilot
  never sees another tenant.
- **Server-key required** — no `COPILOT_ANTHROPIC_KEY`/`ANTHROPIC_API_KEY` → `503`
  and an honest "not configured" banner.
- **Audit** — every executed write action is logged (`action_executed`) and every
  tool call/result is recorded in the session step log.

## Deploy-time gap: the Anthropic key
Unchanged from v1. The Copilot needs a server-side key: it reads
`COPILOT_ANTHROPIC_KEY`, then `ANTHROPIC_API_KEY` (the `_FILE` secret convention
works). Never hardcoded/committed. If neither is set, `/chat` + `/approve` return
`503`. **To enable in production, set `COPILOT_ANTHROPIC_KEY`** (a dedicated key is
recommended for clean cost/usage isolation).

## Credits / cost accounting seam (billing NOT built — integration point)
Future model: **platform fee + free credits** to try tests & chat, **Stripe + PayPal**,
pricing on the landing, subscriptions + invoices. The seam is `credits.py`:
- `check_credits(tenant)` runs before each turn (coarse) AND `agent.py` calls it
  again **before an approved write executes** (deny → the action doesn't run) —
  this is where per-action free-credit accounting plugs in.
- `record_usage(tenant, model, in, out)` records token counts per turn;
  `record_action(tenant, model, action)` records each executed write for
  per-action metering. Neither stores message content.
The v1 provider is a permissive stub (always allowed); swap in a real
`CreditsProvider` with no change to the agent, endpoint, or frontend.

## Frontend
Right-docked slide-out drawer + fixed launcher in `AppShell` (app console only,
lazy-loaded). Streams the agent's answer (Markdown, deep-links that close the
drawer on navigation), shows **live tool activity** rows ("Listing your agents",
"Running certification" with ✓/✕ + a short summary), renders **inline approval
cards** with Confirm/Cancel for write actions, and handles clarifying questions as
ordinary messages. Session continuity via `session_id`. Honest "AI assistant —
may be imperfect" note, focus mgmt + aria + Esc-to-close, reduced-motion friendly,
Chronometer tokens (light/dark).

## Bundle impact
The panel is code-split: its chunk (chat + Markdown renderer, ~9.7 kB / ~3.6 kB
gzip) loads only on first open. Imported solely by `AppShell` (itself lazy), so the
**public landing bundle is unaffected** — `dist/index.html` references neither
`CopilotPanel` nor `AppShell`.

## Config
```yaml
copilot:
  model: claude-sonnet-4-6
  max_output_tokens: 1024
  max_user_chars: 6000
  max_history_messages: 20
  rate_limit_per_minute: 20
```
Session persistence: migration **v23** (`copilot_sessions` table, tenant-scoped).

## Tests
`tests/test_copilot.py` (mocked Anthropic + real tenant-scoped tools, no network):
the agent loop runs a READ tool and answers from real data; a WRITE/COST tool is
NOT executed without an explicit confirm; a denied confirm cancels cleanly; a
confirm executes it and records the action; an injection in a tool result is
neutralized + fenced + secret-scrubbed and cannot trigger an unapproved write or
leak the prompt; the rate limit trips; the credits gate refuses with `402`; token
usage is recorded; an unconfigured server returns `503`; and the honesty
guardrails + semantics are present in the system prompt.
