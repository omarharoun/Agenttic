# Connect your agent

The **Connect your agent** step is the safe, accessible way to point Agenttic at
your *live* agent so the [Safety Battery](../src/agenttic/metrics/safety_battery.py)
can test it and issue an A–F grade + certificate. You give us an HTTP endpoint and
tell us how to talk to it; we send ~14 short safety prompts, read the replies, and
grade them. **No Anthropic key needed** — your agent runs on your own
infrastructure.

> We only ever **send text prompts and read text replies**. We never execute your
> tools, and the connection carries nothing that could run an action on your side.
> This is non-destructive by construction.

---

## The connection, in six steps

1. **Endpoint URL** — the HTTPS URL we POST each prompt to (e.g.
   `https://your-agent.com/v1/chat`).
2. **Auth header** *(optional)* — a single header such as
   `Authorization: Bearer …`. The **value is a secret**: it is encrypted at rest
   (Fernet, the same scheme as your Anthropic key) and is **never returned by the
   API or written to a log** — only a masked `Authorization: ••••1234` is shown.
3. **Preset / mapping** — how the prompt goes into the request body and where the
   reply text is in the response (see below).
4. **Test connection** — we send **one harmless probe**
   (`"Hello, this is a connection test from Agenttic."`) and show you the agent's
   actual reply, or a clear, fix-it error. Nothing from the test is stored.
5. **Authorize** — you confirm *"I own this agent, or I'm authorized to run a
   safety test against it."* A scan is **blocked** until this is recorded.
6. **Scan** — we save the connection (so repeat scans don't re-enter it) and run
   the battery.

---

## Request / response mapping

A prompt has to land in *some* JSON field of your request body, and the reply text
lives at *some* path in the response. You pick a **preset** to fill this in, or map
it yourself.

### OpenAI-compatible (one click)

For any endpoint that speaks the OpenAI Chat Completions shape. Give a base URL, a
model, and a key.

```
Request   POST {endpoint}
          { "model": "<model>",
            "messages": [ { "role": "user", "content": "<prompt>" } ] }

Reply at  choices[0].message.content
```

### Generic webhook

The simplest shape: the prompt goes into one field, the reply comes out of one
field.

```
Request   POST {endpoint}   { "input": "<prompt>" }
Reply at  output
```

You can change the request field (`input`) and the reply path (`output`) to match
your service.

### Custom mapping

Same as generic, but you set both:

- **Request field** — the JSON key the prompt is placed under (e.g. `prompt`,
  `query`, `message`).
- **Reply path** — a dotted path into the response, with `[i]` for list indices.
  Examples: `output`, `data.reply`, `choices[0].message.content`,
  `results[0].text`.

If the reply path doesn't resolve, the test connection tells you exactly what the
top-level response keys were so you can correct it.

> **How battery prompts are rendered.** Each battery case has an instruction and,
> for injection cases, an accompanying document/email. We join them into one
> natural prompt string before placing it into your request field — so your agent
> sees a single, realistic message.

---

## What we send, and how gently

- Every request carries the header **`X-Agenttic-Safety-Test: true`** so you can
  recognise (and, if you like, rate-limit or special-case) our traffic.
- Requests are sent **sequentially — one in flight at a time** — with a
  **per-request timeout** and a **minimum interval** between requests
  (`connect.timeout_s` / `connect.rate_limit_s` in config; defaults 20s / 0.5s).
- The battery is **~14 prompts** total.
- If your agent errors or times out on a prompt, that case is recorded as
  *errored* and **excluded** from the grade (errored ≠ failed) — a flaky endpoint
  doesn't unfairly tank your score.

---

## Safety guarantees

| Guard | What it does |
| --- | --- |
| **SSRF validation** | Both at **save** and at **request** time we reuse [`security.validate_blackbox_url`](../src/agenttic/security.py): only `http`/`https`, and we reject any URL that is — or resolves to — a private, loopback, link-local, reserved, or cloud-metadata (`169.254.169.254`) address. HTTP redirects are disabled so a 3xx can't bounce a validated URL onto an internal target. You can only connect a real, public endpoint. |
| **Consent gate** | A scan against a connected endpoint requires a stored authorization confirmation (`consent` + timestamp). No confirmation → the scan is refused (HTTP 403). |
| **Secret at rest** | The auth header value is encrypted with Fernet, never returned, never logged; only a masked `…last4` is surfaced. |
| **Non-destructive** | We send text and read text. No tool execution path exists in the connection. |
| **Tenant-scoped data** | Response traces are stored per-tenant and follow the existing retention/redaction policy. The auth token and raw secrets are never part of a trace. |

---

## API

All endpoints are under `/api`, authenticated like the rest of the app. Writes
require the `operator` role.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/connect` | Masked status of the saved connection (never the secret). |
| `PUT` | `/api/connect` | Save/update the connection. Validates the URL for SSRF; encrypts the auth value. Body: `endpoint_url`, `agent_name`, `preset`, `request_field`, `response_path`, `model`, `auth_header_name`, `auth_header_value`, `consent`. |
| `DELETE` | `/api/connect` | Remove the saved connection. |
| `POST` | `/api/connect/consent` | Record/clear the authorization confirmation. Body: `consent`. |
| `POST` | `/api/connect/test` | Send one harmless probe; returns `{ ok, reply, error, mapping }`. Stores nothing. |
| `POST` | `/api/scan` with `target: "connection"` | Run the Safety Battery against the saved connection (consent-gated, gentle traffic). |

### Example — connect an OpenAI-compatible agent and scan

```bash
# 1. save the connection (auth value is encrypted at rest)
curl -X PUT $BASE/api/connect -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "endpoint_url": "https://your-agent.com/v1/chat/completions",
  "agent_name": "My support bot",
  "preset": "openai",
  "model": "gpt-4o-mini",
  "auth_header_name": "Authorization",
  "auth_header_value": "Bearer sk-…",
  "consent": true
}'

# 2. confirm it's wired (shows the agent's real reply)
curl -X POST $BASE/api/connect/test -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "endpoint_url": "https://your-agent.com/v1/chat/completions",
  "preset": "openai", "model": "gpt-4o-mini",
  "auth_header_name": "Authorization", "auth_header_value": "Bearer sk-…"
}'
# → { "ok": true, "reply": "Hi! How can I help?", "mapping": { … } }

# 3. scan it (consent already recorded in step 1)
curl -X POST $BASE/api/scan -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{ "target": "connection" }'
# → { "scan_id": "scan_…" }  — poll GET /api/scan/{scan_id}
```
