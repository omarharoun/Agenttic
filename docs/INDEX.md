# Documentation index

Start with the top-level [README.md](../README.md) for the product overview and
[CAPABILITIES.md](../CAPABILITIES.md) for the one-page "what can it do / when do
I use what." This index annotates every doc and how they relate.

## Product & build

| Doc | What it covers | Read it when |
|-----|----------------|-------------|
| [../README.md](../README.md) | Product overview, the standard benchmark track + Agenttic Index, real datasets, key workflows, operational controls | First contact; you want the whole picture |
| [QUICKSTART.md](QUICKSTART.md) | The under-a-minute path: `pip install agenttic` → `agenttic init` → `certify --mock` → a signed grade, plus the one-line `trace()` / `@instrument` usage. Every command is test-executed | You want to try Agenttic right now, no API key |
| [../CAPABILITIES.md](../CAPABILITIES.md) | Capability summary + decision guide ("which workflow do I run") + changelog of major capabilities | You know the product and need to pick the right tool |
| [../SPEC.md](../SPEC.md) | The 10-step build spec the repo implements, with acceptance criteria | You want to know what "done" means per step |
| [CONNECT.md](CONNECT.md) | The "Connect your agent" model: the safe HTTP/webhook contract, request/response mapping + presets (OpenAI-compatible / generic / custom), the safety guards (SSRF, consent, encrypted secret, gentle traffic), and the API | You're wiring a live agent up to be safety-scanned |
| [integrations/](integrations/README.md) | Zero-touch OTel: point an existing exporter (CrewAI, LangGraph, LlamaIndex, OpenAI Agents, generic OTLP) at Agenttic's `/v1/traces` — copy-paste config + honest captured-vs-not per framework, verified by `agenttic doctor` | You already emit OpenTelemetry and want traces in Agenttic with no code change |

## Evaluation methodology

| Doc | What it covers | Read it when |
|-----|----------------|-------------|
| [RESEARCH_TESTING_SURVEY.md](RESEARCH_TESTING_SURVEY.md) | Survey of the agent-eval landscape (BFCL, τ-bench, AgentHarm, AgentDojo, InjecAgent, SWE-bench, GAIA, AssistantBench, FActScore/RAGAS, Inspect, …), the canonical-metric → Agenttic Index mapping, and the prioritized adoption roadmap | You want the literature behind a metric, or to understand why a dataset was/wasn't adopted |
| [INSPECT_INTEROP.md](INSPECT_INTEROP.md) | The Agenttic ⇄ `inspect_ai` `EvalLog` model mapping, what round-trips losslessly, and the lossy edges by design | You want to export evals to Inspect or import a foreign EvalLog |

## Safety, certification & the reference assistant

| Doc | What it covers | Read it when |
|-----|----------------|-------------|
| [SAFE_ASSISTANT.md](SAFE_ASSISTANT.md) | The flagship **Safe Reference Assistant** security model: the OpenClaw-class threat model (prompt injection incl. indirect, secret leakage, sandbox bypass, privilege escalation, resource exhaustion), the five layered defences mapped to those threats, alignment to OWASP GenAI/Agentic + NIST AI RMF, self-certification limits, and a plain-language "what this means for you" — honest about "contain blast radius, not promise immunity" | You want to know *how* an agent is safe, and exactly where the safety claims stop |
| [DEPLOYMENT_SAFETY.md](DEPLOYMENT_SAFETY.md) | The deployment-safety methodology: the four certification dimensions beyond attack-safety (escalation correctness, scope/boundary adherence, exception handling), how they map to standards, and an honest account of what a black-box prompt-and-response scan can and cannot certify (behaviour, not full multi-system orchestration) | You're judging whether a graded agent is *deployment*-safe, not just attack-safe |
| [CERTIFICATION.md](CERTIFICATION.md) | The deterministic A–F safety-grade rubric: dimensions + weights, composite → grade band, the critical-failure cap, the honesty gate, and tamper-evident HMAC issuance / `config_hash` pinning / revocation | You want to understand or verify a safety certificate |

## Operating the platform

| Doc | What it covers | Read it when |
|-----|----------------|-------------|
| [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) | Security/ops readiness review: auth, multi-tenancy, secrets, persistence/migrations, API hardening, observability, SSRF, cost controls, scaling — with what's fixed vs. residual | You're hardening a deployment or assessing risk |
| [OPERATIONS.md](OPERATIONS.md) | Public access via Cloudflare Tunnel, backups (SQLite/Postgres + Litestream), restore drill, data retention & PII controls | You're deploying, backing up, or setting retention |
| [MAIL.md](MAIL.md) | Email for `agenttic.io`: sending via Resend (HTTPS API), receiving via Cloudflare Email Routing | You're wiring up signup verification or support email |
| [BILLING.md](BILLING.md) | The platform-fee + free-credits billing system: the credits ledger, plans/tiers (config), metering the Copilot/scan/certification spend, the out-of-credits 402, Stripe + PayPal setup (which env keys go live), custom invoices, and how it replaces the Copilot stub gate | You're setting up payments, plans, or credits — or need the go-live env keys |

## How the docs relate

- **README → CAPABILITIES → SPEC** is the narrowing path from "what is this" to
  "what does each step do."
- **RESEARCH_TESTING_SURVEY** is the *why* behind the standard track described in
  the README; **INSPECT_INTEROP** implements survey item #5 (Inspect compatibility).
- **PRODUCTION_READINESS, OPERATIONS, MAIL** are the deployment trio — review,
  run, and email-wiring respectively.
- **SAFE_ASSISTANT → DEPLOYMENT_SAFETY → CERTIFICATION** is the safety narrowing:
  the reference agent's *design* (threats → defences), the *methodology* for
  judging deployment safety and its honest limits, and the *rubric* that turns a
  run into a verifiable grade. **CONNECT** is how a live agent is wired up to be
  scanned against them.
