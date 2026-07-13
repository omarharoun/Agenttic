# Agenttic — Agentic Scoring & Benchmarking Platform

A UVM-style verification testbench where the device under test is an **AI
agent**. Agenttic (install `agenttic`; the `agenttic` CLI, with `ascore` kept as
a deprecated back-compat alias) turns business requirements into versioned benchmark suites,
runs any agent against them, scores the runs with deterministic checks plus a
calibrated LLM judge, and produces client-ready scorecards — with a live
monitoring path that detects production drift and triggers re-evaluation.

## Quickstart

**A developer who has never seen Agenttic can `pip install`, add one line, and
get a signed safety grade in under a minute** — no API key:

```bash
pip install agenttic
agenttic init
agenttic certify --mock --out dossier.json   # → a signed Tier A/B/C dossier
agenttic dossier verify dossier.json
```

Then wrap your own agent with one line — `from agenttic import trace; agent =
trace(my_agent)` — or decorate a custom function with `@instrument`. Full
walk-through: [docs/QUICKSTART.md](docs/QUICKSTART.md). Zero-touch OTel setup for
existing exporters: [docs/integrations/](docs/integrations/).

On top of that bespoke-suite engine it now ships a **standard benchmark track**:
seven canonical agent-evaluation metrics rolled into a single **Agenttic Index**,
backed by eight real public datasets (BFCL and its harder splits, τ-bench,
AgentHarm, AgentDojo, InjecAgent, AssistantBench, GAIA, SWE-bench Verified). Plus
an A/B comparison engine, a failure→benchmark hardening loop, a prompt-optimizer,
and Inspect (`inspect_ai`) interop so third parties can re-run your evals in a
harness they already trust.

| UVM concept             | Agenttic component      |
|-------------------------|-------------------------|
| DUT                     | Agent under test        |
| Driver                  | Adapter layer           |
| Monitor                 | Trace capture           |
| Scoreboard              | Scoring engine          |
| Sequence / test         | Test case + rubric      |
| Test plan + coverage DB | Test registry           |
| Checker validation      | Judge calibration       |

## Architecture

```
business inputs ──> benchmark generator ──> test registry (versioned)
                     (LLM + human gate)            │
agents (yours or ──> adapter layer ──> execution harness <┘
 clients', any         │                    │ traces
 framework)      glass-box / black-box      ▼
                                   ┌─ batch evaluation (full rubrics, strong judge)
                                   ├─ standard benchmark (canonical metrics → Index)
                                   └─ live monitoring  (sampled, light judge, drift)
                                            │
                                            ▼
                                     client reporting
        ↻ feedback returns as new tests · drift triggers batch re-evaluation
```

See `docs/architecture.png` for the diagram and `SPEC.md` for the build spec
this repo implements (Steps 1–10, all acceptance criteria covered by tests).
The [Documentation map](#documentation-map) below indexes every doc, and
[CAPABILITIES.md](CAPABILITIES.md) is the one-page "what can it do / when do I
use what" overview.

## Install

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"        # or: pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-...   # needed for generate / run / live judging
```

The CLI reads `ANTHROPIC_API_KEY` from the environment. The **server** is
multi-tenant and uses a per-tenant **BYO Anthropic key** instead — see
[Bring-your-own key](#bring-your-own-anthropic-key).

## Quickstart (no API key needed for the mocked test suite)

```bash
pytest                      # 72 test modules, all LLM calls mocked
```

With a real key, the bespoke-suite operator flow is:

```bash
# 1. Draft a benchmark suite from a business document
agenttic generate job_description.txt --suite-id support-v1

# 2. Human gate: read review/support-v1.md, then
agenttic approve support-v1

# 3. Run an agent against it
agenttic run --agent ref-agent --suite support-v1            # reference agent
agenttic run --agent client-x --suite support-v1 --url http://...  # black-box

# 4. Calibrate the judge against human labels (calibration/support-v1.csv)
agenttic calibrate support-v1

# 5. Deliverable
agenttic report <scorecard_id> -o report.md

# Regression after any agent/model/prompt change
agenttic regress --agent ref-agent
```

A complete hand-written example lives in `examples/pilot_support_triage/`
(10-case support-ticket triage suite + rubric + KB); the end-to-end test
`tests/test_e2e_pipeline.py` runs the entire pipeline on it with mocked
model calls.

## The web front door — the intake interview (`/scan`)

The public site's entry funnel is a single continuous surface. On the landing
page, one line of chips answers the first question before `/scan` even loads
("my agent… handles support / writes code / does research / runs internal
ops"). On `/scan`, Agenttic interviews you — what the agent does, what it can
touch, which failure scares you, where it lives — and each answer writes onto
the **certification profile** panel beside the chat: FOCUS marks land on the
quick-scan safety dimensions and the profile sentence composes as you
answer. The endpoint is the last answer, and the same panel becomes the live
readout: rows flip from FOCUS to pending to pass/fail and the graded seal
stamps in place, with a signed shareable certificate at the end.

Honest by construction: the quick scan always runs every safety dimension — your
focus shapes the report's emphasis, never what gets tested, and the chat says
so. Starting a scan signed-out saves the whole conversation and resumes it
(including auto-starting the scan) after sign-up. A classic paste-a-URL form
stays one click away. Implementation: `ui/src/components/CertConversation.tsx`
(the interview + panel), `ui/src/components/ScanExperience.tsx` (the classic
form and connection manager), backed by `POST /api/scan` in
`src/agenttic/server/routes/scan.py`.

## Standard benchmarks & the Agenttic Index

Bespoke suites answer "is this agent good enough for *this* job." The **standard
track** answers "how does this agent compare on the metrics the research
community already uses." It scores an agent on seven canonical metrics and rolls
them into one **Agenttic Index** (0–100), each metric implementing a published
methodology:

| Metric (`id`)            | Index weight | Methodology / literature |
|--------------------------|:-----------:|--------------------------|
| `tool_call_accuracy`     | 0.28 | Berkeley Function-Calling Leaderboard (BFCL) / τ-bench tool-use: selection, params, sequencing, abstention |
| `reliability_pass_k`     | 0.16 | τ-bench **pass^k** — a case passes only if it succeeds on *all* k runs |
| `faithfulness`           | 0.15 | **FActScore** (Min et al. 2023) / **RAGAS** atomic-claim groundedness — supported-claims fraction |
| `harmful_refusal_rate`   | 0.14 | **AgentHarm**-style refusal: declines harmful requests and doesn't fire forbidden tools |
| `injection_robustness`   | 0.14 | **AgentDojo** / **InjecAgent** prompt-injection resistance (robustness = 1 − attack-success-rate) |
| `calibration_ece`        | 0.08 | **Expected Calibration Error** (Guo et al. 2017) + abstention-appropriateness |
| `answer_accuracy`        | 0.05 | **AssistantBench** (Yoran et al. 2024) fractional partial-credit answer matching |
| `answer_rate`            | 0.00 | AssistantBench abstention — reported as a diagnostic, **unweighted** (weighting it would reward guessing) |

The weights sum to 1.0 over the seven weighted metrics and are renormalized over
whichever components a given run actually produced, so a black-box agent that
can't be scored on faithfulness isn't penalized against a denominator it never
had. Single source of truth: `src/agenttic/metrics/catalog.py`.

**Honesty stance.** The seeded standard suites (`std-tool-use-v1`,
`std-safety-refusal-v1`, `std-safety-injection-v1`, `std-faithfulness-v1`)
implement the *methodology* on Agenttic's own small seed data — they are **not**
the public datasets and don't reproduce any paper's exact numbers. To compare
against the literature you ingest the **real public datasets** (next section).
Either way the Index starts empty: **numbers populate when you run an agent with
your own Anthropic key.**

```bash
agenttic standard seed                 # install the canonical seed suites (idempotent)
agenttic standard metrics              # print the metric catalog + weights
agenttic standard run --agent ref-agent --k 3   # run the standard suites k times → Index
agenttic standard ingest bfcl          # ingest a real dataset suite (see below)
```

API (all under `/api`): `GET /api/standard/metrics`, `GET /api/standard/suites`,
`POST /api/standard/seed`, `POST /api/standard/run`, `GET /api/standard/datasets`,
`POST /api/standard/ingest/{dataset_id}`, `GET /api/standard/leaderboard`. The
public **Methodology** page in the UI (`/methodology`) explains the Index and
links each metric to its source.

## Real public datasets

Eight real public agent benchmarks ingest as standard suites. Each carries its
license, citation, gating status, and any caveat on the dataset card
(`GET /api/standard/datasets`). By default `ingest` loads a small **vendored
sample**; `?full=true` (where the license/gating allows) pulls the full set.

| Suite | Dataset | Tests | License | Gated / caveat |
|-------|---------|-------|---------|----------------|
| `bfcl-simple-v3` (+ `parallel`, `multiple`, `parallel-multiple`, `live-simple`, `live-multiple`) | Berkeley Function-Calling Leaderboard v3 | Tool selection / params / multi-call | Apache-2.0 | — |
| `tau-bench-v1` | τ-bench (Sierra, 2024) | Multi-step tool+user retail/airline | MIT | Methodology only — no user-simulator / stateful DB / official reward |
| `agentharm-harmful-v1` | AgentHarm (ICLR 2025) | Harmful-request refusal | MIT (safety-only clause) | Real harmful prompts **not vendored** — placeholders mirror schema only |
| `agentdojo-v1` | AgentDojo (NeurIPS 2024) | Prompt-injection robustness | MIT | Methodology only — no stateful envs / official `security()` harness |
| `injecagent-v1` | InjecAgent (ACL Findings 2024) | Indirect injection in tool outputs | MIT | Real sample vendored (MIT-compatible) |
| `assistantbench-v1` | AssistantBench (Yoran et al. 2024) | Realistic web-agent QA | Apache-2.0 | Methodology only — no live web environment |
| `gaia-v1` | GAIA validation (Mialon et al., ICLR 2024) | General AI-assistant tasks | CC-BY-4.0 | **Gated** — accept HF terms + set `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` |
| `swebench-verified-v1` | SWE-bench Verified (ICLR 2024) | GitHub-issue code fixes | MIT | **Offline proxy** — scores *patch produced? right files touched?*, **not** the official Docker resolve-rate |

The BFCL splits (`parallel`, `multiple`, `parallel_multiple`, `live_simple`,
`live_multiple`) are the harder v3 tracks: parallel/multiple-call selection and
real user-contributed prompts. Adapters live in `src/agenttic/metrics/datasets/`;
the survey [docs/RESEARCH_TESTING_SURVEY.md](docs/RESEARCH_TESTING_SURVEY.md)
catalogs the wider landscape and why each was (or wasn't) adopted.

> **SWE-bench / execution-gated datasets.** SWE-bench's official metric is
> *resolve-rate* — apply the patch and run `FAIL_TO_PASS`/`PASS_TO_PASS` in a
> per-instance Docker container. Agenttic does **not** run that harness, so
> `swebench-verified-v1` is an explicit **offline proxy**, surfaced as such on
> its dataset card and never presented as the official resolve-rate.

## Key workflows

### A/B compare two agents/prompts/models

Run two variants head-to-head on the **same suite, same rubric, same judge** —
paired, so each case yields one (A, B) outcome. Significance is real statistics,
not eyeballing: **McNemar's test** on the paired pass/fail table (exact binomial
under 25 discordant pairs, χ² with continuity correction above), and a **paired
bootstrap** (2000 resamples, seeded) for per-criterion deltas with 95% CIs. The
verdict is `tie` / `A` / `B`, and underpowered comparisons are labeled as such.

```bash
agenttic ab --suite support-v1 --a ref-agent --b new-prompt \
          --b-prompt "You are a terse support router." --out ab.md
```

API: `POST /api/ab/runs`, `GET /api/ab/runs[/{id}]`,
`GET /api/ab/runs/{id}/report[.pdf]`. Code: `src/agenttic/ab.py`,
`src/agenttic/stats.py`.

### Harden: turn failures into a regression suite

Promote the failing (non-errored) cases from any scorecard into a permanent,
versioned **regression suite** (deterministic id `regress--{agent}--{source}`),
de-duplicated by fingerprint and carrying provenance back to the original case
and failure reason. Re-run it after a fix and get a per-case delta
(improved / regressed / same / new), with McNemar applied across paired re-runs.

API: `GET /api/hardening/candidates`, `POST /api/hardening/promote`,
`GET /api/hardening/suites[/{id}]`, `POST /api/hardening/rerun`. In the UI this
is the **Hardening** page. Code: `src/agenttic/hardening.py`.

### Optimize a system prompt (with an overfitting guard)

An OPRO/ProTeGi-style loop: reflect on the judge's failing-criterion rationales
(the "gradient"), propose N candidate prompts, A/B each against the current best
on a **train split**, and adopt a candidate only if net pass-rate improves *and*
no criterion significantly regresses. A held-out split the optimizer never sees
exposes overfitting (`overfit_gap = train_gain − heldout_gain`). The model is
frozen; only the prompt text changes. Bounded and cost-aware (round/candidate/run
caps with an up-front run projection).

```bash
agenttic optimize --suite support-v1 --agent ref-agent \
                --prompt-file base_prompt.txt --rounds 2 --candidates 3 \
                --heldout 0.3 --max-runs 60 --out best_prompt.txt
```

API: `POST /api/optimize/runs`, `GET /api/optimize/runs[/{id}]`. Code:
`src/agenttic/optimizer.py`.

### Export to / import from Inspect (`inspect_ai`)

Bidirectional bridge to UK AISI [Inspect](https://inspect.aisi.org.uk/)'s
`EvalLog` format — no runtime dependency on `inspect_ai`; the emitted JSON
validates against the documented schema. Export is lossless for Agenttic-origin
records (spans preserved under `sample.metadata`); import of foreign logs snaps
scores to Agenttic's `{0, 0.5, 1}` scale. Lets third parties re-run your evals in
a harness they trust and opens the `inspect_evals` catalog for comparison.

```bash
agenttic inspect-export <scorecard_id> --out scorecard.json
agenttic inspect-import scorecard.json --save
```

API: `GET /api/scorecards/{id}/inspect.json`. Full mapping and lossy edges:
[docs/INSPECT_INTEROP.md](docs/INSPECT_INTEROP.md).

## Simulating business workflows with Managed Agents (beta)

Anthropic's [Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview)
hosts the agent loop and a sandboxed container per session — which means you
can stand up a candidate business workflow internally, with zero agent
infrastructure, and immediately benchmark it:

```bash
# 1. Describe the workflow step as a version-controlled agent YAML
#    (see examples/pilot_support_triage/workflow.agent.yaml)
# 2. Deploy it (creates the agent once; re-deploys bump the immutable version)
agenttic deploy examples/pilot_support_triage/workflow.agent.yaml

# 3. Benchmark it like any other agent — one session per test case
agenttic run --agent triage-wf --suite pilot-support-triage \
           --managed-agent-id agent_01... --environment-id env_01...
```

The `ManagedAgentAdapter` converts the session's live event stream into a
standard glass-box Trace: model requests become `llm_call` spans (with token
usage from `span.model_request_end`), tool use/result pairs become `tool_call`
spans, `session.error` becomes an error span — so the **full rubric applies**,
unlike black-box HTTP agents. The adapter pins the exact agent version it tested
(via `GET /v1/agents/{id}`) into the trace's config hash, so a regression after a
prompt tweak is attributable to that version bump, and the agent's model feeds
Hard Rule 4 judge selection automatically.

## Visual workflow builder (n8n-style UI)

```bash
npm --prefix ui install && npm --prefix ui run build   # once
uv run agenttic pilot                                    # seed the demo suite (DRAFT)
uv run agenttic ui                                       # http://127.0.0.1:8700
```

An n8n-style canvas over the whole platform: drag nodes from the palette
(business doc → generator → human gate; agent → run suite → score → scorecard →
report; live monitor; FI Evaluation), wire typed ports (mismatched kinds refuse
to connect), configure nodes in the side panel (forms generated from each node's
pydantic schema), then **Run**. Nodes animate live over SSE — the run node shows
`7/10 cases`, the gate node parks the execution with an ✋ **Approve** button
(durable across server restarts), failures mark downstream nodes skipped. Other
pages: the 🏆 Index leaderboard, the Standard/Methodology explainer, Hardening,
executions history, and resource browsers for suites, scorecards, and traces.

Dev mode: `uv run agenttic ui` + `npm --prefix ui run dev` (Vite on :5173,
proxies `/api`). The engine is headless-first: workflows are documents
(`POST /api/workflows`), executions stream `GET /api/executions/{id}/events`, so
CI can run the same graphs without the canvas.

## Agenttic Index (leaderboard)

A leaderboard that ranks agents across suites, in the spirit of
[artificialanalysis.ai](https://artificialanalysis.ai/)'s Intelligence Index.
Each **suite is a benchmark**; an agent's **Index** is the weighted mean of its
per-suite task-success rate (0–100), using the latest scorecard per (agent,
suite). The UI's 🏆 page shows a ranked table (Index, blended $/case, p95
latency, suite coverage, tier) and an Index-vs-cost scatter; a common-set filter
restricts the board to shared suites for an apples-to-apples comparison. Per-suite
weights live in `config.yaml` (`leaderboard.suite_weights`). API:
`GET /api/leaderboard?suites=a,b`. Comparison is honest about coverage — an agent
is ranked on what it actually ran, never silently averaged over different
denominators. The standard-track board (`GET /api/standard/leaderboard`) ranks by
the canonical Agenttic Index instead.

## Agents: declared catalog + discovery

The agent set is open-ended — any endpoint/config/prompt is a new agent — so the
platform **discovers** agents descriptively from runs: `GET /api/agents` unions
every agent observed in scorecards and traces (plus deployed Managed Agents), and
the 🤖 Agents page lists them. Nothing needs registering for an agent to show up.

On top of that, you can **declare** agents you run repeatedly — pre-register a
name, variant, and connection details once, then pick them when configuring a run:

```bash
agenttic agents add prod-bot --variant blackbox --url https://prod/agent
agenttic agents add triage --variant reference --model claude-sonnet-4-6 \
                 --system-prompt "You are a support-ticket router."
agenttic agents list                       # the catalog (latest version each)
agenttic run --agent prod-bot --suite support-v1   # connection details resolved
```

The catalog is versioned and append-only in the registry like everything else
(`agenttic agents retire` is a soft-delete that keeps history). CRUD API:
`GET/POST /api/agents/catalog`, `GET/DELETE /api/agents/catalog/{id}`.

## Scoring backends

Each rubric criterion is scored by one of three backends:

- **`code`** — deterministic checks (`final_output_matches_expected`,
  `required_tool_called`, the canonical-metric checks in
  `src/agenttic/metrics/canonical_checks.py`, …).
- **`judge`** — the tiered LLM judge (Sonnet executor consulting an Opus advisor
  on borderline calls).
- **`fi`** — [Future AGI](https://github.com/future-agi/future-agi)'s open-source
  `ai-evaluation` metric library (groundedness, toxicity, relevancy, …). Set
  `scorer: fi` + `fi_metric: <name>` on a criterion, or drop an **FI Evaluation**
  node on the canvas. FI's 0–1 score is discretized into the criterion's
  binary/three-point scale (Hard Rule 3), keeping the raw value + reason in the
  rationale. Optional dependency: `uv pip install agenttic[fi]`; the default metric
  set is offline (cloud LLM-judge metrics need `FI_API_KEY`/`FI_SECRET_KEY`).

**Partial batch scoring:** if a case can't be scored (judge/FI outage, bad check
config), it becomes an *errored* run — kept and surfaced (`errored_test_ids`, an
amber "not scored" row in the Results panel), but excluded from
`task_success_rate` and per-criterion means, so a scoring-infra failure never
masquerades as the agent failing the task. Execution cost/latency still count
every run.

## Operational controls (auth, cost, hardening)

```yaml
auth: {required: true, token: ""}     # prefer the ASCORE_API_TOKEN env var
security:
  rate_limit_per_minute: 120          # 0 = off; per token/IP
  blackbox_block_private: true        # SSRF guard for black-box agent URLs
budget:
  max_run_cost_usd: 5.0               # abort a run that would exceed this
  max_daily_cost_usd: 50.0            # 0 = unlimited
```

- **Auth.** Set `ASCORE_API_TOKEN` (or `auth.token`) and every `/api` route —
  including the SSE stream (`?token=`) and the human-approval gate — requires it.
  `auth.required: true` makes the server refuse to start without a token.
- **SSRF guard.** Black-box agent URLs are validated at registration and at
  request time: http/https only, no private/loopback/link-local/metadata targets,
  no redirects.
- **Rate limiting.** A per-client sliding-window cap on `/api`.
- **Cost estimation & ceilings.** Pricing lives in `config.yaml` (`pricing`).
  Before a run, `agenttic` projects spend (agent + judge); actual cost (execution
  **and** judge tokens) is recorded on every scorecard and shown in the report.
  The `budget` caps abort a run that would exceed the per-run or daily ceiling.

See [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) for the full gap
analysis and [docs/OPERATIONS.md](docs/OPERATIONS.md) for deploy/backup/retention.

### Bring-your-own Anthropic key

The CLI uses the process-global `ANTHROPIC_API_KEY`. The **server is
multi-tenant**: each tenant stores its own Anthropic key, encrypted at rest
(AES-256-GCM, key derived from `auth.session_secret`); only the last 4 chars are
kept in clear for masking. Keys are never logged or returned by the API. Every
run uses the tenant's key — there is no platform fallback — so a missing key
returns `400 "Add your Anthropic API key in Settings to run tests"`. Code:
`src/agenttic/server/keys.py`.

## Programmatic access: personal API tokens + run-a-test over REST

Drive the whole platform from scripts/CI **as your own account**. In
**Settings → API keys**, create a *personal API token* (PAT) — an `agt_…` value
shown once, stored only as a SHA-256 hash, mapped to your tenant + role. Send it
as `Authorization: Bearer agt_…` and every `/api` endpoint authenticates as you.
Revoking it in Settings takes effect immediately.

**Auth precedence:** an explicit bearer / `X-API-Key` / `?token=` always wins
over a session cookie. Among explicit tokens, a configured shared/admin token
(`ASCORE_API_TOKEN`) is matched first, then PATs. PATs are distinct from your
Anthropic key (which still powers the actual model calls — set it first or runs
return `400`). Code: `src/agenttic/server/pats.py`; auth wiring in `server/auth.py`.

```bash
export AGENTTIC_TOKEN=agt_…            # created in Settings → API keys
AUTH="Authorization: Bearer $AGENTTIC_TOKEN"
BASE=https://agenttic.io

# 1) generate a benchmark from a business requirement AND start the run
EXEC=$(curl -s -X POST $BASE/api/quickstart/from-requirement -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"requirement":"The support agent must never reveal another customer'\''s data.",
       "agent_id":"my-agent","system_prompt":"You are a careful support agent."}')
EID=$(echo "$EXEC" | python -c 'import sys,json;print(json.load(sys.stdin)["execution_id"])')

# 2) poll → the human gate pauses for approval; 3) approve to continue
curl -s $BASE/api/executions/$EID -H "$AUTH"            # status: waiting_approval
curl -s -X POST $BASE/api/executions/$EID/approve -H "$AUTH"

# 4) poll until succeeded, then fetch joined results + the scorecard
curl -s $BASE/api/executions/$EID/results -H "$AUTH"
curl -s $BASE/api/scorecards/$SC -H "$AUTH"             # JSON scorecard
curl -s $BASE/api/scorecards/$SC/report.pdf  -H "$AUTH" -o report.pdf
curl -s $BASE/api/scorecards/$SC/inspect.json -H "$AUTH" -o inspect.json

# --- or skip generation: run the standard (canonical) suites ---
curl -s -X POST $BASE/api/standard/seed -H "$AUTH"
curl -s -X POST $BASE/api/standard/run  -H "$AUTH" \
  -H "Content-Type: application/json" -d '{"agent_id":"my-agent","k":3}'
curl -s $BASE/api/standard/leaderboard  -H "$AUTH"
```

`POST /api/quickstart/from-requirement` is a thin convenience endpoint that
builds the canonical generate→approve→run→score→report pipeline server-side so
you don't hand-author the graph. The full reference (with copy-paste curl) lives
at [`/api-docs`](https://agenttic.io/api-docs).

### Result caching (don't re-spend on identical runs)

A run's result is fully determined by its inputs, so identical runs are cached
instead of re-executed. The cache key is `sha256(agent_id + suite_id/version +
agent config_hash + rubric_id/version + judge models)`, **per tenant** (a tenant
only ever hits its own results — no cross-tenant leakage). On a hit the prior
scorecard is returned with **zero agent/judge calls (`$0`)** and `"cached": true`
in the response — no human-gate approval and no Anthropic key required, since
nothing runs. Caching is applied on all run paths: the workflow executor
(Run Suite step short-circuits), `/quickstart/from-requirement` (which also
derives a deterministic suite id from the requirement so re-runs reuse the
generated suite), and `/standard/run`. Bypass with `?force=true` (or
`"refresh": true`). The mapping lives in the append-only registry
(`result_cache` table, migration v10); browse past results — fresh vs cached, with
cost — on the **Results** page (`/app/results`) or via `GET /api/scorecards`.
Code: `src/agenttic/result_cache.py`, cache hooks in `src/agenttic/server/nodes.py`.

## Design rules the code enforces

1. **The trace schema is the contract.** Changes bump `SCHEMA_VERSION`.
2. **Judge criteria without pass/fail anchors are invalid** — rejected at
   model-validation time, so they can never reach scoring.
3. **Binary or three-point scales only.** Scores outside {0, 0.5, 1} are
   rejected by the schema.
4. **Judge model ≠ agent model.** Enforced in the judge constructor and the
   config loader.
5. **Agent mistakes are data.** Tool errors become error spans; the harness
   retries transport failures only, never agent behavior, and persists every
   trace including timeouts.
6. **Uncalibrated judge scores are provisional** and labeled as such in every
   scorecard and report.
7. **All model names, thresholds, and rates live in `config.yaml`.**
8. **Everything is versioned and append-only** — re-running suite vN reproduces
   byte-identical inputs forever.

Black-box agents (a bare HTTP endpoint) are scored on the criteria that don't
require trajectory data, and their reports carry an explicit tier banner;
instrumenting an agent for glass-box traces unlocks the full rubric.

## Documentation map

| Doc | Covers |
|-----|--------|
| [CAPABILITIES.md](CAPABILITIES.md) | One-page capability summary + "when do I use what" |
| [SPEC.md](SPEC.md) | The 10-step build spec this repo implements |
| [docs/INDEX.md](docs/INDEX.md) | Annotated index of every doc |
| [docs/RESEARCH_TESTING_SURVEY.md](docs/RESEARCH_TESTING_SURVEY.md) | Landscape survey of agent benchmarks + adoption roadmap |
| [docs/INSPECT_INTEROP.md](docs/INSPECT_INTEROP.md) | Agenttic ⇄ `inspect_ai` EvalLog mapping |
| [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) | Security/ops readiness review + residuals |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Cloudflare Tunnel, backups, restore, retention/PII |
| [docs/MAIL.md](docs/MAIL.md) | Email (Resend send + Cloudflare receive) setup |

## Layout

```
src/agenttic/
├── schema/        # Step 1 — Pydantic contracts (trace, testcase, rubric, scorecard)
├── adapters/      # Steps 2 & 7 — base driver, reference agent, black-box HTTP, managed
├── harness/       # Step 3 — async runner: timeouts, transport-only retries
├── scoring/       # Steps 4 & 5 — checks registry, LLM judge, calibration, engine, fi_eval
├── metrics/       # canonical metrics + Agenttic Index + real dataset adapters
├── registry/      # Step 6 — SQLite/Postgres, append-only versioning
├── generator/     # Step 8 — business doc -> draft suite (human gate)
├── live/          # Step 9 — sampled production scoring + drift detection
├── reporting/     # Step 10 — client scorecard reports (Markdown/PDF)
├── interop/       # Inspect (inspect_ai) EvalLog export/import
├── ab.py · hardening.py · optimizer.py · stats.py   # A/B, hardening loop, prompt-optimizer
├── ops.py         # shared pipeline ops (CLI and UI both call these)
├── server/        # workflow engine + FastAPI/SSE API for the UI (+ keys, auth, tenancy)
└── cli.py         # agenttic command surface (incl. `agenttic ui`, `standard`, `ab`, `optimize`)
ui/                # React front end (Vite): public site + intake interview (/scan),
                   # certification surfaces, and the React Flow workflow canvas (/app)
```

## Status & roadmap

All 10 spec steps implemented with their acceptance criteria as tests
(`pytest`, 72 test modules, all LLM calls mocked) plus a CI workflow
(`.github/workflows/ci.yml`). On top of the spec: the standard benchmark track
(seven canonical metrics → Agenttic Index, eight real public dataset adapters),
the A/B comparison engine, the failure→benchmark hardening loop, the
prompt-optimizer, Inspect interop, a public methodology page, the visual workflow
builder, and the declared-agent catalog over the discovery model. Natural next
increments: OpenTelemetry GenAI export/import for the trace schema, more framework
adapters (LangGraph, OpenAI Agents), an HTTP ingest endpoint for the live path,
a containerized execution harness for SWE-bench's official resolve-rate, and
per-engagement suite libraries.

## Certification track (SPEC-2 → SPEC-6)

The certification track turns an evaluation into a **verifiable evidence dossier**
— a hash-chained, offline-verifiable record of what an agent was tested on, how it
scored, what was **NOT ASSESSED**, and the resulting Tier (A/B/C). It is honest by
construction: `cbrn_proxy` stays NOT ASSESSED (no novel harmful content is ever
generated), a provisional judge caps the tier at B, and elicitation inconsistency
(sandbagging) caps it further — all disclosed in the dossier.

### Quickstart

```bash
# 1. Inspect the shipped safety profile (composition, pinned suites, coverage)
agenttic profiles show cert-agent-safety-v1        # cbrn_proxy renders NOT ASSESSED

# 2. Certify an agent → an evidence dossier (offline demo, no API key)
agenttic certify --agent ref-agent --profile cert-agent-safety-v1 --mock -o /tmp/dossier.json

# 3. Verify the dossier offline (recomputes hashes; names the offending ref on tamper)
agenttic dossier verify /tmp/dossier.json

# 4. Renew (chained dossier; $0 if the agent is unchanged) / revoke (append-only)
agenttic certify --agent ref-agent --profile cert-agent-safety-v1 --renew --mock
agenttic dossier revoke <dossier_id> --reason "safety regression"
```

Server: `POST /api/certify` (async job) → `GET /api/dossiers/{id}` /
`…/report.pdf`; **public** `GET /certification/{dossier_id}` verifies from the
dossier JSON alone. Incidents: `agenttic incidents open|list|report|close|export`
with S1–S4 SLA clocks (`docs/INCIDENT_CROSSWALK.md`). Regulatory mapping:
`docs/REGULATORY_CROSSWALK.md` (evidence, **not** a compliance determination).

See `AGENTTIC-MASTER-PLAYBOOK.md` for the full spec and
`docs/SPEC2_BASELINE.md` / `docs/SPEC2_DEVIATIONS.md` for the build record.

### Attribution
Agent cards and the Catalog are derived from **The 2025 AI Agent Index** (Zenodo,
DOI 10.5281/zenodo.19592546, CC BY 4.0). See [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md).
Index-derived data is Catalog-only and never mixed into measured scores.

## Agent cards (SPEC-2 M9–M10)

Provenance-tracked agent descriptions on the AI Agent Index taxonomy. Every value
is `measured` (evidence refs), `documented` (citations), or `attested` (signature)
— **no refs ⇒ no value**; `none_found` ≠ `confirmed_none` (confirming absence needs
evidence). Autonomy is classified L1–L5 conservatively (unclassifiable ⇒ None), and
a covered-agent detector flags agentic systems (True/False/None). A covered agent
without a card caps its certification tier at B; frontier autonomy (L4/L5) adds
required domains and tightens floors.

```bash
agenttic cards autofill ref-agent     # measured fields from traces/scorecards/dossiers
agenttic cards show ref-agent
agenttic cards annotate ref-agent -f company_accountability.developer -v Acme -c https://acme.com
```

Public: `GET /cards/{agent_id}` (renders from card JSON alone, provenance classes
distinct), `GET /catalog`. Index import brings in the CC BY dataset as Catalog-only
`documented` cards — never mixed into score leaderboards.

## Enforcement gateway (SPEC-2 M11–M13)

An inline gateway compiled from certification evidence guards an agent's tool
calls: hash-verified policy load → **Lane 1** (deterministic allow/deny, action
classes, egress SSRF, rate ceilings) → **Lane 2** (injection quarantine with the
original preserved, secret/PII redaction) → append-only log → **Lane 3** (async
judge — never inline). Write-class actions fail closed; every fail-open is logged.

Policies are **compiled** (`enforce/compiler.py`) from the dossier tier + caps,
the card's autonomy, incidents, and staleness — deterministic, tighten-only,
recompiled on evidence change. Approvals park a call and resolve with PAT identity;
resolutions become measured card evidence.

```
POST /api/enforce/sessions          # hash-verified policy load
POST /api/enforce/tool-call         # → Decision (allow/deny/transform/require_approval)
POST /api/enforce/tool-result       # injection screen → quarantine
GET  /api/enforce/dashboard         # block rate, fail-open count, approval latency
GET  /api/enforce/export?fmt=otel   # OTel-GenAI spans (no payloads)
```

Public verify/card pages render "enforced under policy `<hash>`" + posture from
the compiled policy alone.

## Staged release + canaries + oversight (SPEC-2 M14–M15)

Agents are served through an ordered **release ladder** (internal → vetted →
limited → ga) with tightening posture per stage; access is stage-gated (callers
above the agent's promoted stage are denied). Promotion is **evidence-gated**
(observation hours, incident ceiling, tier) and one stage at a time — forced
promotion is impossible; an open S1/S2 auto-demotes immediately.

**Honeypot canaries** plant decoy tools + credentials + tripwire domains; any use
is a confirmed positive → deny + S1 incident. Canaries never touch certification
scorecards and rotate while preserving trip history.

**Oversight analytics** track approval-process health (latency, approval rate,
rubber-stamp indicator). An opt-in **interactive RL loop** (`agenttic oversight
watch`) surfaces borderline decisions to a human and adapts posture via a
contextual bandit — auto-tightening, but only ever *proposing* loosening behind an
explicit confirmation. All disabled by default.

## Passport, receipts + verifier SDK (SPEC-2 M16–M17)

Agents carry a short-lived, **Ed25519-signed passport** bound to their latest
certification evidence (tier, dossier hash, policy hash, stage, autonomy). Keys
are published as a JWKS at `/.well-known/agenttic-jwks.json` and rotate with
overlap. Verification is split from status — a valid signature on a **revoked**
passport is rejected.

Every allowed action can carry a **signed receipt** (bound to a logged
allow-decision; hashes not payloads); delegation chains resolve to the human
principal. A relying party verifies passports/receipts **offline** with the
Python or JS **verifier SDK** (no Agenttic account) — agents self-identify via the
`Agent-Passport` header.

An authenticated **risk feed** (`GET /api/feeds/risk/{agent_id}`) exposes
aggregate posture for underwriters/procurement (no traces/PII), with **webhooks**
on tier change, revocation, S1/S2 incidents, and stage demotion.
