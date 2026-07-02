# Capabilities & "when do I use what"

A one-page map of what Agenttic (`ascore`) can do and which workflow to reach for.
For the full narrative see [README.md](README.md); for the doc map see
[docs/INDEX.md](docs/INDEX.md).

## What it does

| Capability | In one line | Entry points |
|------------|-------------|--------------|
| **Bespoke benchmark suites** | Turn a business doc into a versioned, human-gated test suite and score any agent on it | `ascore generate / approve / run / report` |
| **Standard benchmark track** | Score an agent on seven canonical metrics → one **Agenttic Index** | `ascore standard seed / run / metrics` · `GET /api/standard/*` |
| **Real public datasets** | Ingest BFCL (+5 splits), τ-bench, AgentHarm, AgentDojo, InjecAgent, AssistantBench, GAIA, SWE-bench Verified as suites | `ascore standard ingest <id>` · `GET /api/standard/datasets` |
| **A/B comparison** | Two variants head-to-head, paired, with McNemar + bootstrap significance | `ascore ab` · `POST /api/ab/runs` |
| **Hardening loop** | Promote failing cases into a versioned regression suite; re-run for per-case deltas | `GET/POST /api/hardening/*` (Hardening page) |
| **Prompt-optimizer** | OPRO/ProTeGi-style system-prompt search with a held-out overfitting guard | `ascore optimize` · `POST /api/optimize/runs` |
| **Inspect interop** | Export/import evals to UK AISI `inspect_ai` `EvalLog` (no `inspect_ai` dependency) | `ascore inspect-export / inspect-import` · `GET /api/scorecards/{id}/inspect.json` |
| **Live monitoring** | Sampled production scoring with a light judge + drift detection → re-eval trigger | `ascore monitor` · `/api/live/*` |
| **Managed Agents (beta)** | Deploy a workflow YAML to Anthropic Managed Agents and benchmark it glass-box | `ascore deploy` · `ascore run --managed-agent-id` |
| **Visual workflow builder** | n8n-style canvas over the whole platform, live SSE, durable human gates | `ascore ui` |
| **Leaderboards** | Rank agents by suite Index or by the canonical Agenttic Index, honest about coverage | `/api/leaderboard` · `/api/standard/leaderboard` |
| **Agent catalog + discovery** | Agents are discovered from runs; declare the ones you reuse | `ascore agents add/list` · `/api/agents` |

## The Agenttic Index, at a glance

Seven weighted canonical metrics (weights sum to 1.0; renormalized over whichever
components a run produces). Source of truth: `src/ascore/metrics/catalog.py`.

| Metric | Weight | Methodology |
|--------|:-----:|-------------|
| Tool-call accuracy | 0.28 | BFCL / τ-bench tool-use |
| Reliability pass^k | 0.16 | τ-bench (passes only if all k runs pass) |
| Faithfulness | 0.15 | FActScore / RAGAS atomic-claim groundedness |
| Harmful-refusal rate | 0.14 | AgentHarm refusal |
| Injection robustness | 0.14 | AgentDojo / InjecAgent (1 − attack-success-rate) |
| Calibration (ECE) | 0.08 | Guo et al. 2017 + abstention |
| Answer accuracy | 0.05 | AssistantBench fractional matching |
| _Answer rate_ | _0.00_ | AssistantBench abstention — diagnostic, unweighted |

**Honesty:** the seeded standard suites implement these *methodologies* on
Agenttic's own seed data — they are not the public datasets and don't reproduce
any paper's numbers. The Index is empty until you run an agent with your own
Anthropic key. SWE-bench is scored by an **offline proxy**, not its official
Docker resolve-rate — the real resolve-rate harness is now **wired but gated**
(set `ASCORE_SWEBENCH_HARNESS=docker` in an environment with Docker + the
`swebench` package + instance images) rather than hard-disabled. The honest
per-wedge status — reproduced vs proxy vs seed-sample, and what real reproduction
would take — is served at `GET /api/public/reproduction`. As of now **no wedge
reproduces a public leaderboard number in this environment**, and that endpoint
says so plainly instead of hiding it.

**Red-team injection (real probe set, honest self-test):** the
`redteam-injection-v1` suite is a genuine, technique-diverse prompt-injection
probe set (28 probes across 12 techniques — indirect document/email/web/tool,
direct override, encoded/obfuscated payloads, role-play jailbreaks, system-prompt
exfiltration, delimiter escapes, payload splitting, zero-width Unicode — plus
benign controls). `GET /api/public/redteam/injection` also runs a **self-test of
the lexical injection detector** against a labeled response corpus and publishes,
honestly, what it MISSES: on the seed corpus it catches ~44% of hijacks and
misses the evasion tail (paraphrased compliance, synonym tools, exfiltration)
entirely. Catching those needs the real AgentDojo / InjecAgent execution
environments — a tracked future task, not something we claim to already do.

**Calibration (demonstrated, seed):** the deterministic heuristic checks
(refusal, injection robustness, secret-leak, faithfulness gate) are calibrated
against a shipped human-label corpus — run `uv run ascore calibrate-corpus` or
`GET /api/public/calibration` to reproduce it (offline, no key). The v1 seed
corpus shows ~0.88 overall agreement, with intentional tail cases the lexical
checks miss surfaced rather than hidden. It is a *small seed set*, not a large
inter-annotator study, and it does **not** cover the **LLM judge** — so every
judge criterion is marked **PROVISIONAL/uncalibrated** in scorecards until a
judge-vs-human run is done (SPEC Hard Rule 6).

## Which workflow do I run?

- **"Is this agent good enough for this client's job?"** → bespoke suite:
  `generate → approve → run → report`.
- **"How does this agent stack up on community metrics?"** → standard track:
  `standard seed → standard run`, or `standard ingest` a real dataset first.
- **"Did my prompt/model change actually help?"** → `ascore ab` (paired,
  significance-tested) — don't eyeball two scorecards.
- **"This agent failed these cases — make sure they stay fixed."** → hardening:
  promote failures → regression suite → `rerun` for deltas.
- **"Find me a better system prompt."** → `ascore optimize` (with a held-out
  split so you don't fool yourself).
- **"Let another team re-run my evals in their harness."** → `inspect-export`.
- **"Catch production drift."** → `ascore monitor` (live, sampled).
- **"Benchmark a workflow before any production code exists."** → `ascore deploy`
  a Managed Agent YAML, then `run`.

## Capability changelog (major additions over the 10-step spec)

Newest first. See `git log` for the precise commits.

- **SWE-bench Verified adapter** — offline proxy (patch-produced / file-localized);
  official resolve-rate is execution-gated and out of scope.
- **Prompt-optimizer** — self-improving system-prompt loop with overfitting guard.
- **GAIA adapter** — gated general AI-assistant benchmark (validation split).
- **AssistantBench** — web-agent suite + fractional answer-accuracy / answer-rate
  metrics (the 7th and diagnostic Index components).
- **Hardening loop** — failure → versioned regression suite → delta re-run.
- **Inspect interop** — `inspect_ai` `EvalLog` export/import + endpoint/CLI.
- **BFCL harder splits** — parallel / multiple / parallel_multiple / live_simple /
  live_multiple as standard suites.
- **AgentDojo + InjecAgent adapters** — real indirect prompt-injection datasets.
- **Faithfulness metric** — FActScore/RAGAS atomic-claim groundedness, promoted
  from deferred (weight 0) to a live Index component (weight 0.15).
- **Public Methodology page** — explains the Index and links each metric to its
  source.
- **A/B engine** — McNemar + paired bootstrap, report + PDF.
- **Research survey** — the agent-eval landscape + adoption roadmap.

## Cost & key model

Model calls need an Anthropic key. The **CLI** uses `ANTHROPIC_API_KEY`; the
**server** is multi-tenant and uses a per-tenant **BYO key** stored encrypted
(no platform fallback). `pytest` mocks every model call, so the test suite runs
with no key. Standard runs cost roughly `k ×` the per-run tokens (k = pass^k
repetitions); cost is projected before a run and recorded on every scorecard.
