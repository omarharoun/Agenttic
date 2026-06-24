# Documentation index

Start with the top-level [README.md](../README.md) for the product overview and
[CAPABILITIES.md](../CAPABILITIES.md) for the one-page "what can it do / when do
I use what." This index annotates every doc and how they relate.

## Product & build

| Doc | What it covers | Read it when |
|-----|----------------|-------------|
| [../README.md](../README.md) | Product overview, the standard benchmark track + Agenttic Index, real datasets, key workflows, operational controls | First contact; you want the whole picture |
| [../CAPABILITIES.md](../CAPABILITIES.md) | Capability summary + decision guide ("which workflow do I run") + changelog of major capabilities | You know the product and need to pick the right tool |
| [../SPEC.md](../SPEC.md) | The 10-step build spec the repo implements, with acceptance criteria | You want to know what "done" means per step |
| [CONNECT.md](CONNECT.md) | The "Connect your agent" model: the safe HTTP/webhook contract, request/response mapping + presets (OpenAI-compatible / generic / custom), the safety guards (SSRF, consent, encrypted secret, gentle traffic), and the API | You're wiring a live agent up to be safety-scanned |

## Evaluation methodology

| Doc | What it covers | Read it when |
|-----|----------------|-------------|
| [RESEARCH_TESTING_SURVEY.md](RESEARCH_TESTING_SURVEY.md) | Survey of the agent-eval landscape (BFCL, τ-bench, AgentHarm, AgentDojo, InjecAgent, SWE-bench, GAIA, AssistantBench, FActScore/RAGAS, Inspect, …), the canonical-metric → Agenttic Index mapping, and the prioritized adoption roadmap | You want the literature behind a metric, or to understand why a dataset was/wasn't adopted |
| [INSPECT_INTEROP.md](INSPECT_INTEROP.md) | The Agenttic ⇄ `inspect_ai` `EvalLog` model mapping, what round-trips losslessly, and the lossy edges by design | You want to export evals to Inspect or import a foreign EvalLog |

## Operating the platform

| Doc | What it covers | Read it when |
|-----|----------------|-------------|
| [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) | Security/ops readiness review: auth, multi-tenancy, secrets, persistence/migrations, API hardening, observability, SSRF, cost controls, scaling — with what's fixed vs. residual | You're hardening a deployment or assessing risk |
| [OPERATIONS.md](OPERATIONS.md) | Public access via Cloudflare Tunnel, backups (SQLite/Postgres + Litestream), restore drill, data retention & PII controls | You're deploying, backing up, or setting retention |
| [MAIL.md](MAIL.md) | Email for `agenttic.io`: sending via Resend (HTTPS API), receiving via Cloudflare Email Routing | You're wiring up signup verification or support email |

## How the docs relate

- **README → CAPABILITIES → SPEC** is the narrowing path from "what is this" to
  "what does each step do."
- **RESEARCH_TESTING_SURVEY** is the *why* behind the standard track described in
  the README; **INSPECT_INTEROP** implements survey item #5 (Inspect compatibility).
- **PRODUCTION_READINESS, OPERATIONS, MAIL** are the deployment trio — review,
  run, and email-wiring respectively.
