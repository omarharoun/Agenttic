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
per-wedge status — reproduced vs scorer-validated vs proxy vs seed-sample, and
what real reproduction would take — is served at `GET /api/public/reproduction`.
As of now **no wedge reproduces a public per-model leaderboard number in this
environment** (no `ANTHROPIC_API_KEY` to generate predictions; SWE-bench needs
the Docker harness), and that endpoint says so plainly instead of hiding it.

**BFCL — the grader is reproduced/validated on the full real dataset.** For the
tool-calling wedge (Berkeley Function-Calling Leaderboard), BFCL grades by
deterministic AST match — no LLM judge, no Docker. Our AST grader is proven
against the **whole real `simple` category (n=400)**: the oracle (ground-truth)
predictions score **100%** (Wilson 95% [0.9905, 1.0]), and a wrong prediction
scores wrong — so the machinery that reproduces the leaderboard number is correct.
The one missing input is the *model's* predictions, which need an API key.
Reproduce the grader (offline, no key) or run a real model with:

    uv run ascore reproduce-bfcl --split simple --full            # validate grader
    uv run ascore reproduce-bfcl --split simple --model <M> \
        --predictions <preds.json> --published <ACC> --published-source <URL>

The wedge is honestly labelled **`scorer_validated`** (not `reproduced`) until a
real per-model run overlaps its published number.

**Red-team injection (real probe set, honest self-test):** the
`redteam-injection-v1` suite is a genuine, technique-diverse prompt-injection
probe set (28 probes across 12 techniques — indirect document/email/web/tool,
direct override, encoded/obfuscated payloads, role-play jailbreaks, system-prompt
exfiltration, delimiter escapes, payload splitting, zero-width Unicode — plus
benign controls). `GET /api/public/redteam/injection` also runs a **self-test of
the injection detector** against a labeled response corpus and publishes, honestly,
what it catches vs misses. The detector is now **hardened**
(`metrics/injection_detect.py`): beyond exact target-token / tool-name matching it
adds structural, technique-aware layers — synonym-family tool calls,
paraphrased/decoded action-completion, privilege grants, and system-prompt
exfiltration — with a strengthened resistance guard so genuinely-robust agents
aren't false-flagged. On the labeled corpus this lifts detection from **~44%
(lexical only) to ~87%** at **0% false-positive rate**, and it still honestly
reports the tails it **misses** (non-English compliance; hijacks via unknown tool
families / novel completion verbs). Closing those needs the real AgentDojo /
InjecAgent execution environments or a model-based classifier layer (needs an API
key) — wired but not required, and never faked.

**Calibration (demonstrated, seed).** Two corpora, both shipped; run
`GET /api/public/calibration` (or the CLIs) to reproduce.

- *Deterministic heuristic checks* — calibrated **offline, no key** against an
  expanded **51-record** human-label corpus (`uv run ascore calibrate-corpus`);
  overall agreement **~0.84**. Demonstrated-calibrated: `harmful_action_refused`
  (0.82), `no_secret_leak` (1.0), `faithfulness_grounded` (0.80), and
  **`tool_misuse_safety` (0.875, newly covered)**. `injection_robust` honestly
  measures **0.75** on the harder expanded sample (a paraphrase / non-English
  tail + one false positive) — **below threshold, so it stays PROVISIONAL** (not
  tuned away). Intentional tail disagreements are surfaced, not hidden.
- *LLM judge* — calibrating the judge means RUNNING it (an LLM), which needs a
  model API key. The corpus (`helpfulness`, `tone_professional`,
  `faithfulness_judge`, 15 labeled records) + runner are **wired**:
  `uv run ascore calibrate-judge` (needs `ANTHROPIC_API_KEY`; est. **~$0.07**).
  With no key nothing is run or faked — every judge criterion stays
  **PROVISIONAL/uncalibrated** in scorecards until a real judge-vs-human run
  demonstrates agreement (SPEC Hard Rule 6).

Both corpora are *small seed sets*, not large inter-annotator studies — labelled
as such.

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
