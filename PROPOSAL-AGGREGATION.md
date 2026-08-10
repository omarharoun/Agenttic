# Aggregating the open-source eval ecosystem — a strategy memo

**Status:** proposal. No code changes accompany this document.
**Date:** 2026-08-08.
**Audience:** the maintainer, and a technical collaborator who will help decide and (maybe) build.
**Question it answers:** "I want agenttic to be the biggest agent-testing platform, and I want to
integrate every open-source testing platform into mine." Should agenttic aggregate the ecosystem,
and if so, how — and where does that stop being worth it?

The landscape facts below come from live web research (GitHub REST API, PyPI/npm, official docs)
on 2026-08-08. Where a fact could not be verified at the source it is flagged **[unverified]**.
The codebase facts come from reading the repo directly; file references are clickable.

---

## 1. Bottom line

The framing "build an aggregation layer" is wrong, because **the aggregation layer already exists.**
Agenttic has a documented evaluator interface (`EvaluatorAdapter` → normalized `EvalResult` →
`AggregateReport`), a working reference adapter for AISI Inspect, a deployment-aware license gate,
and entry-point plugin discovery. That is the hard part, and it is built.

The problem is that **the aggregation layer is orphaned from the one part of agenttic that is
defensible.** The certification pipeline — signed dossier, ABOM, tier decision, revocation — is
built from a different object (`Scorecard`) and never touches the union. A single signed passport
attesting to "the union of many evaluators" is the module's stated thesis, and it is not wired to
the signer. So today, agenttic can aggregate evaluators *or* it can certify a run, but the two do
not meet.

This reframes the whole proposal:

- **The work is wiring, not building** — connect `AggregateReport` to `certify.py`, persist it as
  evidence, and record each source in the ABOM. Bounded and describable (§3).
- **On "integrate everything": no.** The cost argument (§4) is decisive. Agenttic already has both
  ingestion patterns in-tree, and they cost radically different amounts to maintain. The path that
  reimplements each benchmark's scorer (BFCL is already ~1,000 lines of agenttic-maintained port)
  does not scale and, in a certification product, is actively dangerous: a drifted scorer means
  signing a claim you can no longer reproduce. Own a bounded set of ingest adapters; do not own an
  unbounded tail of scorer reimplementations.
- **But there is a real limit the thesis must not dodge (§5):** when agenttic ingests someone
  else's scored result, the signature is a *provenance receipt* ("Inspect vN, this config, this
  agent build, returned X"), not an independent verdict. That is honest and certifiable — but it is
  worth less than an independent judgment, and the strong independent judgment is exactly the
  expensive path we are arguing to shrink. This tension is the crux of whether the thesis holds.
- **The cheapest test of the whole thesis is a sprint, not a quarter (§9):** wire the existing
  Inspect adapter through to a signed dossier, produce one real artifact, and put it in front of
  3–5 prospective buyers with one question — "is a signed, revocable, reproducible bundle of
  someone else's benchmark result worth paying for, or do you need us to be the judge?"

Your original read — defensible position is the certification layer, consume others' benchmarks,
own a documented interface plus 2–3 reference adapters rather than a long tail — is **substantially
correct and partly already implemented.** The research supports it. The one place it needs
sharpening is the signature question, which decides how much the "consume" strategy is actually
worth.

---

## 2. Where agenttic sits today vs. what exists

### 2.1 What agenttic actually has (verified by reading the repo)

There are **three result shapes** in the codebase, and this is the source of most of the
confusion:

| Shape | File | What it is | Wired to certification? |
|---|---|---|---|
| `Scorecard` | [schema/scorecard.py](src/agenttic/schema/scorecard.py) | Criterion-based run result: `CriterionScore` (scorer ∈ code/judge/fi), `task_success_rate` + Wilson interval, assertions, coverage, signoff | **Yes** — `certify.py` reads `scorecard_refs` |
| `EvalResult` / `AggregateReport` | [schema/eval_result.py](src/agenttic/schema/eval_result.py), [evaluators/orchestrator.py](src/agenttic/evaluators/orchestrator.py) | Normalized per-probe row with mandatory provenance (source/version/SPDX license), controlled `dimension` vocab, `oracle` kind, `raw` verbatim; aggregated per-source with coverage | **No** — orphaned |
| `WedgeReproduction` | [metrics/reproduction.py](src/agenttic/metrics/reproduction.py) | Honest per-benchmark reproduction status (reproduced / reproduced_recorded / proxy / seed_sample) | Reporting only |

And **two ingestion patterns**, which differ in *where the scoring happens*:

- **`DatasetAdapter`** ([metrics/datasets/base.py](src/agenttic/metrics/datasets/base.py)) — pull a
  public dataset, map records into `TestCase`s preserving ground truth in `expected`, declare a
  `Rubric` of canonical checks, run the agent, and **score it with agenttic's own canonical-check
  ports** → `Scorecard`. *Agenttic owns the runner and the scorer.* Already wired for bfcl,
  tau-bench, agentharm, injecagent, agentdojo, assistantbench, gaia, swebench
  ([metrics/datasets/__init__.py](src/agenttic/metrics/datasets/__init__.py)).
- **`EvaluatorAdapter`** ([evaluators/base.py](src/agenttic/evaluators/base.py)) — wrap an external
  evaluator that runs and scores *itself*, normalize its verdicts into `EvalResult`, keep its output
  verbatim in `raw` → `AggregateReport`. *Agenttic owns only a versioned mapping table, not the
  scorer.* Built for `agenttic-gen` (first-party) and Inspect
  ([evaluators/inspect_adapter.py](src/agenttic/evaluators/inspect_adapter.py)).

The `EvaluatorAdapter` path is genuinely well-designed and mostly done:

- The Protocol is minimal and stable: `id` / `version` / `license` (SPDX) + `capabilities()` +
  `run()` ([evaluators/base.py:124](src/agenttic/evaluators/base.py)).
- Provenance is non-optional and validated in `__post_init__`; the `dimension` vocabulary is owned
  by agenttic and adapters must *map* onto it, never invent
  ([schema/eval_result.py:102](src/agenttic/schema/eval_result.py)).
- Un-run dimensions are stamped `not_assessed`, never an assumed pass
  ([evaluators/orchestrator.py:309](src/agenttic/evaluators/orchestrator.py)).
- There is **no bare blended score** — the only way to get an index is
  `index_with_breakdown()`, which forces the per-source decomposition and coverage to travel with it
  ([evaluators/orchestrator.py:180](src/agenttic/evaluators/orchestrator.py)).
- The **license gate** is deployment-aware and records why each source was allowed to contribute —
  permissive runs everywhere; source-available/AGPL refused when `hosted`, relaxed when
  `self_hosted`; unknown fails closed when hosted
  ([evaluators/license_gate.py](src/agenttic/evaluators/license_gate.py)).
- Third parties ship adapters as separate packages under the `agenttic.evaluators` entry-point group;
  a broken plugin is skipped, not fatal
  ([evaluators/orchestrator.py:57](src/agenttic/evaluators/orchestrator.py)).

That is a real aggregation layer. It is the thing your prompt proposed building.

### 2.2 The honest gaps

- **The union is orphaned from the signature.** `certify.py` assembles the dossier from
  `scorecard_refs` and an `inspect_log_ref` *string*
  ([certification/certify.py:316](src/agenttic/certification/certify.py)); it never imports
  `run_evaluation`, `AggregateReport`, or `EvalResult`. The "many evaluators → one signed passport"
  claim is currently library-only. (This matches the internal note that the Evaluator Plugin
  Interface is "library-only, not deployed.")
- **The union is safety-only.** The controlled vocabulary is five safety/quality dimensions —
  `injection_robustness`, `harmful_refusal`, `tool_safety`, `secret_disclosure`, `faithfulness`
  ([schema/eval_result.py:42](src/agenttic/schema/eval_result.py)); the orchestrator's headline is
  literally "Union **Safety** Index." Capability benchmarks (SWE-bench resolve-rate, BFCL AST
  accuracy, GAIA answer accuracy, tau2 task success) do not map onto those five dimensions, so today
  they flow through the *other* path (`DatasetAdapter` → `Scorecard`). "Consume everyone's
  benchmarks through the union" therefore does not yet span capability. The schema is built to be
  extended (`DIMENSION_VOCAB_VERSION`, "extend by MINOR bump"), so this is a decision, not a rewrite
  — but it is a decision to make consciously (§6).
- **The Inspect adapter is a stub, honestly labelled.** It runs an offline, deterministic battery of
  six items across two dimensions and marks the version `+offline-stub`; the live
  `inspect_ai.eval(...)` seam exists but is deliberately unwired
  ([evaluators/inspect_adapter.py:174](src/agenttic/evaluators/inspect_adapter.py)). So the reference
  adapter proves the *interface*, not yet a real Inspect run.
- **Reproduction is honestly weak, and the repo says so.** No wedge reproduces a public leaderboard
  number *live* in this environment; BFCL has a *recorded* reproduction (Claude Sonnet 4.5, n=400
  Python simple, 97.5% vs published 97.75%) and the SWE-bench wedge is scored by an offline proxy
  because the Docker resolve harness is gated
  ([metrics/reproduction.py](src/agenttic/metrics/reproduction.py)). The metric catalog states
  plainly that adopting public datasets for direct comparability is "a NEXT phase"
  ([metrics/catalog.py:1](src/agenttic/metrics/catalog.py)).

### 2.3 What exists in the ecosystem (verified 2026-08-08)

The full table is in the appendix. The load-bearing findings:

- **The best agent harness to consume is Inspect** (AISI). MIT, shipping to PyPI almost daily
  (`inspect-ai` 0.3.253 on 2026-08-08), purpose-built for multi-step tool-using agents, with a
  structured `.eval` log and a companion `inspect_evals` repo of 200+ prebuilt evaluations —
  including reimplementations of BFCL, GAIA, and GDPval. Agenttic already chose it as the reference
  adapter. **One well-maintained Inspect adapter is a force multiplier: it is a single dependency
  that transitively reaches dozens of benchmarks.**
- **The most credible meta-aggregator archived its harness but kept its product.** HAL (Princeton's
  Holistic Agent Leaderboard) — one harness across nine benchmarks scoring accuracy *and cost* —
  **archived its `hal-harness` GitHub repo on 2026-07-01** (verified: the repo page carries the
  banner "This repository was archived by the owner on Jul 1, 2026. It is now read-only"; the API
  returns `archived: true`) and paused leaderboard submissions. But the **project is alive and
  pivoting**: the public leaderboard remains up, work has moved to a Reliability Dashboard, the paper
  was accepted to ICLR 2026, and HAL joined the AI Evaluator Forum. The lesson is precise and cuts
  *for* the thesis: they retired the commodity part — the universal multi-benchmark *runner* — and
  kept the differentiated parts — cost-aware ranking, reliability, and published logs.
  Undifferentiated aggregation is the treadmill; the wrapper is the product. (Full treatment, the
  record correction, and what is worth borrowing clean-room: §10.)
- **Everything worth consuming is permissively licensed,** with named exceptions to route around:
  Phoenix's *server* is Elastic-2.0 (source-available; its `phoenix-evals`/`client`/`otel`
  subpackages are Apache-2.0), Langfuse is open-core (MIT except `ee/`), LangSmith's *platform* is
  proprietary (only its SDKs are MIT), and among benchmarks GAIA is gated with an unclear license
  **[unverified]** and SWE-bench Pro deliberately uses GPL repos for its OSS split. Agenttic's
  license gate already encodes exactly this distinction.
- **The most-used benchmark is the most contaminated.** OpenAI publicly retired SWE-bench Verified
  as a frontier metric **[secondary-sourced; the post returned 403]**; reported failure modes
  include solution leakage and weak tests. For a *certification* product this matters more than for
  a leaderboard: signing "resolved 68% of SWE-bench Verified" is signing a number the field no longer
  trusts.

---

## 3. The orphan, and what wiring it to `certify.py` actually requires

This is the core of the proposal. Not "build aggregation" — **connect the aggregation you have to
the signature you have.**

Today's certification flow (verified in [certification/certify.py](src/agenttic/certification/certify.py)
and [certification/dossier.py](src/agenttic/certification/dossier.py)):

```
suite + rubric + judge/canonical checks → Scorecard (persisted, has an id)
      → certify.py reads scorecard_refs + inspect_log_ref (a string)
      → tiers.decide() (tier_decision.evidence_refs must resolve to persisted ids)
      → signing gate reads the scorecard's stored signoff block
      → dossier.assemble() → content_sha256, chain to prev, ABOM, attestation → signed passport
      → verify_dossier() recomputes the hash offline; revoke() flips status
```

The `AggregateReport` has no on-ramp to any of that. Wiring it requires, concretely:

1. **Persist the union as first-class evidence.** `Scorecard` has an id and a registry home;
   `EvalResult`/`AggregateReport` do not. Add a registry object (an `AggregateReport` id, its rows,
   its coverage, its gate decisions) so a dossier can *reference* it the way it references a
   scorecard. This is the single missing primitive.
2. **Teach `dossier.assemble` to accept an aggregate-evidence ref** and ensure
   `tier_decision.evidence_refs` resolve to it — `verify_dossier` already fails a dossier whose tier
   decision cites no resolvable evidence
   ([certification/dossier.py:125](src/agenttic/certification/dossier.py)), so this is a real
   constraint, not decoration.
3. **Do not touch the promotion gate's behavior.** This is a hard rule ("Never change scoring-engine
   behaviour or the Step 14 promotion gate"). The signing gate reads a scorecard's `signoff`
   verification block; the union report carries coverage + per-source + `not_assessed` but *not* the
   assertion/signoff shape the gate consumes. So the safe move is to make the union satisfy the
   *existing* evidence contract (a thin adapter from `AggregateReport` coverage → the evidence the
   gate already reads), **not** to teach the gate a new verdict rule. If that adaptation turns out to
   require changing the gate's pass/fail logic, stop and reconsider — that is the line the hard rule
   draws.
4. **Record each source in the ABOM.** [certification/abom.py](src/agenttic/certification/abom.py)
   is the Agent Bill of Materials; the license gate already emits per-source
   (source, version, SPDX, classification, decision, reason) via `GateDecision.to_dict()`. Enumerate
   every evaluator that contributed as an ABOM component. This is low-risk and is where most of the
   "signed provenance" value actually lands.
5. **Add a per-source reproduction pin.** For any vendor-scored source, store what would be needed to
   re-run it (evaluator version, config, model id, seed). Without this, `agenttic verify` can confirm
   the *hash* but not *re-execute* the evidence — and for a vendor oracle, re-execution is the whole
   reproducibility claim (§5).

Items 1–2 and 4 are a few hundred lines of glue over already-built machinery. Item 3 is the one that
needs care and the one where the hard rule bites. Item 5 is the honesty tax that makes the signature
mean something.

---

## 4. The cost argument: ingest the oracle vs. reimplement it

This is the strongest concrete thing in the memo, because both patterns already exist in-tree and we
can just count.

**Reimplementing the scorer (`DatasetAdapter`).** To carry BFCL as an agenttic-scored benchmark,
the repo maintains:

- [metrics/bfcl_ast_official.py](src/agenttic/metrics/bfcl_ast_official.py) — 232 lines, a *faithful
  port* of BFCL's official AST checker (string normalization, int→float, optional/unexpected-param
  handling).
- [metrics/bfcl_reproduce.py](src/agenttic/metrics/bfcl_reproduce.py) — 529 lines of runner and
  scorer-validation.
- [metrics/datasets/bfcl.py](src/agenttic/metrics/datasets/bfcl.py) — 240 lines of dataset adapter.

≈ **1,000 lines of agenttic-owned code to reproduce one benchmark.** And the repo already documents
the drift hazard directly: the faithful port scored the same predictions **97.5%** while a simpler
homegrown grader scored them **93.75%** — a ~4-point swing that "was entirely the grader,"
i.e. BFCL's documented normalization rules, not the model
([metrics/reproduction.py:77](src/agenttic/metrics/reproduction.py)). That is the danger in one
sentence: **a small divergence in a reimplemented scorer moves the headline number by points.**

BFCL is now on **v4** (released 2025-07-17), with the lineage v1 (AST) → v2 (live APIs) → v3
(multi-turn, state-based) → v4 (agentic + web search). Every one of those transitions is a chance
for a maintained port to fall behind the real checker. In a leaderboard product, a stale port is an
out-of-date number. **In a certification product, a stale port means the signed claim ("BFCL AST
97.5%") no longer reproduces against the real checker — you have signed something you can no longer
defend on challenge.** That is not a maintenance annoyance; it is a direct hit to the one thing the
product sells.

SWE-bench makes the point from the other side: agenttic *did not* reimplement the resolve-rate
oracle, because the real one is a Docker execution harness (apply patch, run FAIL_TO_PASS /
PASS_TO_PASS). Instead it runs an honest offline *proxy* (was a patch produced? did it touch the gold
files?) and labels it a proxy ([metrics/datasets/swebench.py](src/agenttic/metrics/datasets/swebench.py),
[metrics/swebench_resolve.py](src/agenttic/metrics/swebench_resolve.py)). That is the correct
instinct — but a proxy is not a certifiable resolve-rate, and closing that gap means running the
upstream harness, i.e. *consuming* it, not reimplementing it.

**Ingesting the oracle (`EvaluatorAdapter`).** The Inspect adapter consumes an entire external
harness in **308 lines** ([evaluators/inspect_adapter.py](src/agenttic/evaluators/inspect_adapter.py)),
and the only agenttic-owned semantic surface is a versioned dictionary — `INSPECT_CATEGORY_TO_DIMENSION`,
a handful of entries ([evaluators/inspect_adapter.py:57](src/agenttic/evaluators/inspect_adapter.py)).
When Inspect changes its scorer internals, that is Inspect's problem; agenttic keeps the verdict
verbatim in `raw` and can always show what the tool said. The maintenance surface is the mapping
table and the arm's-length import, not a thousand-line port that must chase every upstream release.

**The asymmetry, stated plainly:** reimplementing is O(one large port per benchmark) that you must
keep bit-for-bit in sync with an upstream that changes on its own schedule; ingesting is O(one small
mapping per harness) plus a dependency you pin. For an "integrate everything" ambition, the first is
a tax that compounds with every benchmark and every upstream release; the second is bounded. The
existing BFCL port is the cautionary example, not the template. **Recommendation: freeze the
scorer-port path at what exists, and add new coverage through vendor-oracle ingestion.**

---

## 5. What is agenttic actually certifying? (the question the thesis must answer)

If agenttic ingests Inspect's scored output and signs it, is it certifying *Inspect's* verdict or
*its own*? Dodging this makes the memo worthless, so here is the answer the code already implies.

**In the ingest path, agenttic certifies the evidence and its provenance — not an independent
judgment.** The `EvalResult` schema is built for exactly this: `source` + `source_version` +
`source_license` are mandatory, `oracle="vendor"` marks the verdict as not agenttic-adjudicated, and
`raw` preserves the evaluator's original output verbatim
([schema/eval_result.py:82](src/agenttic/schema/eval_result.py)). So the honest signed claim is:

> "Evaluator *Inspect v0.3.253 (MIT)*, run under *this pinned config* against *agent build
> `config_hash=…`*, returned *these verdicts*. Here is the union across sources, here is the coverage
> (X of Y dimension×source cells assessed, the rest `not_assessed`), and here is the raw output.
> Agenttic did not independently re-judge each item."

That is a **notarization**, and it is genuinely valuable *if and only if* two things hold:

1. **The bundle is reproducible.** A signature over evidence that cannot be re-executed is a
   reproducibility claim in name only — which is close to the thing agenttic exists to prevent. For
   deterministic oracles (SWE-bench tests, tau2 state checks, Cybench flags, BFCL AST) this is
   achievable. For model-graded oracles (Inspect *live*, DeepEval, Ragas, the tau2 user-simulator)
   it needs pinned model + seed + config, costs money per re-run, and is nondeterministic at the
   margins. The repo's own reproduction report already concedes no wedge reproduces live without a
   key. **So the ingest-path signature is strongest over deterministic oracles and weakest over
   model-graded ones** — and much of the ecosystem is drifting toward model-graded.
2. **Notarization is worth paying for.** A buyer who wants *certification* may want an independent
   judge, not a signed copy of someone else's homework — especially when that someone else's harness
   is MIT and the buyer could run it and self-attest. This is the real objection, and it is in §8.

**The other path signs a stronger claim.** In the `DatasetAdapter` path, agenttic scores with its
own checker (validated against the gold oracle) → the claim is "*we* judged this, and here is our
calibrated scorer." That is a more valuable signature — and it is exactly the expensive,
drift-prone path §4 argues to shrink. **There is no free lunch: the cheap path yields the weaker
signature; the strong signature needs the expensive path.** The strategic bet is that the
certification wrapper — gaming resistance, judge calibration, ABOM, revocation, honest coverage —
carries enough value on *top* of a notarized third-party verdict that customers pay for the bundle
even when the underlying oracle is someone else's. That bet is testable (§9) and should be tested
before much more is built.

---

## 6. Which harnesses to adapt first, and why

Two or three reference adapters, maintained well, per your instinct. Chosen for leverage,
license, and reproducibility of the oracle — not popularity.

1. **Inspect (`inspect_ai`) — finish and wire the one you already started.** Highest leverage move
   in the whole plan. It is MIT, the most actively developed agent harness in the ecosystem, and its
   `inspect_evals` companion reaches dozens of benchmarks (BFCL, GAIA, GDPval reimplementations
   included) through a *single* maintained dependency. The adapter, the mapping table, and the
   `.eval`-log read path already exist; the work is (a) wire the live `inspect_ai.eval` strategy
   that is currently a stub, and (b) run its `AggregateReport` through to a signed dossier (§3).
   Doing this first also *is* the cheapest thesis test (§9).

2. **tau2-bench — one full external runner with a deterministic oracle.** MIT, pip-installable,
   ships a `tau2 run` CLI, and judges by **environment-state + action-sequence checks** (not an LLM
   judge for the reward), with **pass^k built in** — which matches agenttic's existing
   reliability/pass^k emphasis. It proves the interface against a *real runner* rather than an
   offline battery, and its deterministic reward makes it a strong reproducibility case for the
   signature. Caveat to record in the ABOM: the *user* is LLM-simulated, so cross-run comparability
   depends on a pinned user-simulator model.

3. **DeepEval — cover the RAG/judge-metric world your future customers already run.** Apache-2.0,
   pytest-native, results are clean Python objects with per-metric scores and reasons, and it does
   span-level agentic tracing. Adapting it proves the interface generalizes beyond
   safety/agent-trajectory harnesses into the faithfulness/quality metrics that RAG-heavy teams use
   daily, and it maps naturally onto the existing `faithfulness` dimension. Its oracle is
   LLM-as-judge, so it is the honest stress test of the "model-graded reproducibility" limit in §5.

**And the explicit non-goal:** do not keep expanding `metrics/datasets/*` scorer ports. Where an
upstream ships a runnable harness (BFCL via `inspect_evals`, SWE-bench via its Docker harness, tau2
via its CLI), migrate onto vendor-oracle ingestion or freeze the port; do not add new ~1,000-line
ports (§4).

*(If a third slot should go to reach rather than metric-breadth, Terminal-Bench/**Harbor**
[Apache-2.0, container test-script oracle] is the alternative to DeepEval — but note Harbor is itself
a "run any agent" meta-runner, which overlaps agenttic's own subject-adapter ladder and is therefore
competition-adjacent. DeepEval is the safer third pick.)*

---

## 7. Maintenance burden, and what breaks when upstream changes

**Per-adapter cost, from the two patterns in-tree:**

- *Vendor-oracle adapter (recommended):* ~300 LOC once (cf. Inspect's 308), then a small versioned
  mapping table + a pinned dependency. Ongoing cost is tracking the harness's *output format* and
  occasionally its category taxonomy.
- *Scorer-port adapter (avoid for new work):* ~1,000 LOC (cf. BFCL), then permanent chase of the
  upstream scorer's *semantics*. Ongoing cost is bit-for-bit fidelity to a checker that changes on
  someone else's schedule.

**What breaks, by failure mode:**

- **Upstream changes its output schema.** The mapping table stops parsing. Detected loudly (rows
  become `error`, not silent passes — the no-crash rule already guarantees this), so it degrades to
  `not_assessed` rather than a false verdict. Cheap to fix. This is the *good* failure mode.
- **Upstream changes its scorer semantics.** For a *port*, the number silently drifts and you sign a
  claim that no longer reproduces (§4) — the *dangerous* failure mode. For an *ingest* adapter, the
  drift is upstream's and you keep their verbatim output, so your provenance stays truthful even if
  their number moved.
- **Upstream churns fast.** Inspect ships to PyPI almost daily; the OTel GenAI *agent* span
  conventions are still experimental with no stabilization timeline **[secondary-sourced]**. Pinning
  is mandatory; "low maintenance" is relative, not zero.
- **Even a funded team retired its universal harness.** HAL archived its `hal-harness` repo
  (2026-07-01, read-only) and stopped taking submissions, while keeping the leaderboard and pivoting
  to reliability (§10). A parallel-VM harness orchestrating nine benchmarks was heavy enough to
  retire the *code* even as the *project* thrived — direct support for the core recommendation:
  consume upstream runners, do not build and own a universal one.
- **Upstream is abandoned or moved.** Real and recent: Ragas moved orgs (`explodinggradients` →
  `vibrantlabsai`) and is ~6 months stale, WebArena is maintenance-mode with activity shifted to
  BrowserGym/AgentLab, SWE-Lancer moved into OpenAI's `preparedness` monorepo. An adapter to a dead
  harness is dead weight. This is the argument *for* a small, curated set: three adapters you can
  afford to babysit beats fifteen you cannot.
- **Upstream re-licenses.** The gate already handles the static case; the risk is a *change*
  (e.g. a permissive tool adopting BSL). Because the gate is deployment-aware and records its
  decision, a re-license degrades a source to `refused` in `hosted` mode rather than creating a
  compliance incident — but someone has to notice and bump the recorded SPDX.

**The meta-lesson from HAL:** the maintenance burden of a *universal harness* is precisely why the
most credible attempt at one archived the code — while keeping the differentiated wrapper (cost-aware
ranking, reliability, published logs) running. Whatever agenttic builds must be *narrow enough to
maintain* and *wrapped in something the raw harnesses do not provide* (signature, revocation, ABOM,
calibration) — and it should expect that wrapper to be contested, because HAL is now building an
overlapping one (§10).

---

## 8. The strongest case against — stated properly

Not a strawman. If any of these is decisive, the aggregation thesis is wrong.

1. **A notarized third-party verdict may be unsellable.** The ingest path signs "Inspect said X"
   (§5). A buyer who wants certification may want an *independent* judge. Worse: Inspect is MIT, so
   the buyer can run it themselves and self-attest — agenttic's signature then adds only
   notarization + revocation, not judgment. If buyers will not pay a premium for notarization over a
   commodity harness they could run for free, the "consume everyone's benchmarks" strategy collapses
   into "be a nicer benchmark runner," and the moat narrows to the parts where agenttic owns the
   oracle — i.e. the expensive path we just argued to shrink. **This is the load-bearing risk.**

2. **The strong signature needs the expensive path.** The cheap ingest path yields the weak
   (notarization) signature; the strong (independent-judgment) signature needs agenttic to own and
   maintain calibrated scorers — the ~1,000-line ports with drift risk. The strategy is internally
   in tension: it wants low maintenance *and* a strong signature, and §5 shows you cannot have both
   on the same benchmark.

3. **Model-graded oracles undermine the reproducibility that justifies the signature.** Much of the
   ecosystem is moving to LLM-as-judge (DeepEval, Ragas, MLflow's Agent-GPA, the tau2 user-sim). A
   signed bundle over a nondeterministic, key-gated, paid oracle can be hash-verified but not cheaply
   re-executed — exactly the "trust me" posture agenttic exists to replace. The signature is only as
   strong as the oracle's reproducibility.

4. **A funded competitor already occupies the differentiated ground.** The case against is *not*
   that aggregation is a graveyard — that was a misread of HAL, corrected in §10. It is closer to the
   opposite: HAL kept exactly the parts agenttic wants to own. Its public leaderboard already ranks
   agents by **accuracy *and cost*** (a Pareto frontier — "agents can be 100× more expensive while
   only 1% better"), it publishes **~2.5B tokens of raw agent logs** for inspection, it uses
   LLM-aided log analysis to catch gaming (agents that "search for the benchmark on HuggingFace
   instead of solving the task"), and it is formalizing via ICLR 2026 and the AI Evaluator Forum.
   Cost-aware, inspectable, reliability-focused agent evaluation from a Princeton group is a serious
   neighbor. Agenttic's distinct claim over HAL is the **signed, revocable certificate** — a
   narrower differentiator than "we aggregate everything." Treat HAL as a competitor to out-execute
   on the certification axis, not a cautionary tale.

5. **The benchmarks themselves are decaying signals.** SWE-bench Verified — the most-cited one — was
   publicly retired as a frontier metric over contamination **[secondary-sourced]**. Aggregating
   more contaminated benchmarks produces more signed numbers that sophisticated buyers already
   discount. Breadth of coverage is not the same as trustworthiness of coverage, and the product
   sells the latter.

---

## 9. What would have to be true for this to fail — and the cheapest test

**This fails if any of these is true:**

- Buyers want an independent verdict, not a notarized third-party one (kills §5/§8.1).
- Vendor-scored runs cannot be reproduced cheaply on challenge, so revocation/verification is
  theater (kills §5.1).
- Upstream harnesses churn or die faster than one maintainer can track even 2–3 adapters (kills the
  "bounded tail" premise, §7).
- Buyers self-attest with the same free MIT harness and will not pay for notarization (kills the
  willingness-to-pay, §8.1).

**The cheapest early test — a sprint, and it tests the whole thesis:**

Wire the *existing* Inspect adapter through to a *signed, verifiable dossier* on the reference agent,
offline (`AgentTarget.reference()` already exists —
[evaluators/base.py:67](src/agenttic/evaluators/base.py)). The hard parts — Protocol, `EvalResult`,
orchestrator, license gate, dossier signing, offline verification, revocation — are already built;
you are connecting two finished things (§3) plus a per-source reproduction pin. Output: one real
artifact — a signed passport whose evidence is "Inspect vN said X, agenttic did not adjudicate,
coverage C, raw attached," that `agenttic verify` accepts and `revoke` can flip.

Then do the non-code half, which is the actual test: put that artifact in front of **3–5 prospective
buyers** and ask one question —

> "Is a signed, revocable, reproducible bundle of *someone else's* benchmark result worth paying for
> — or do you need *us* to be the judge?"

The answer decides the strategy:

- *"The notarized bundle is valuable"* → the ingest thesis holds; invest in adapters #2 and #3 and
  the vocab extension for capability (§6, §2.2), keep the scorer-port tail frozen.
- *"We need you to be the judge"* → the moat is agenttic's own calibrated oracle, not aggregation;
  invest in judge calibration and a small number of *owned* scorers, and treat ingest as a
  convenience feature, not the strategy.

Either outcome is worth knowing, and both are reachable for the price of a sprint plus five
conversations — because the platform is already built. That is the whole point of this memo: the
expensive infrastructure exists; what is missing is one wire and one answer.

---

## 10. HAL — correcting the record, and what's worth taking (clean-room)

This section exists because the first draft got a fact wrong, and because the true picture turns out
to contain the best borrowable ideas in the whole memo.

### 10.1 Correction of the record (verified 2026-08-08)

The first draft said HAL "was archived 2026-07-01 with no successor" and leaned on it as evidence
that the meta-aggregator concept is dead. That was wrong. What is actually true:

- **The `princeton-pli/hal-harness` GitHub repo is archived.** The repo page shows the banner *"This
  repository was archived by the owner on Jul 1, 2026. It is now read-only,"* and the API returns
  `archived: true`, last push 2026-07-01, 310★ / 61 forks. The README states HAL "is no longer
  accepting new result submissions through this harness" and is "retiring active PRs."
- **The HAL *project* is alive and has pivoted.** The public leaderboard (hal.cs.princeton.edu)
  remains up (most recent models ~Aug–Sep 2025; updates paused), work moved to an **agent-reliability
  dashboard** ("consistency, predictability, robustness, safety, self-awareness"), the paper was
  **accepted to ICLR 2026** [per the leaderboard site], and HAL joined the **AI Evaluator Forum**
  [per Princeton CITP news]. This is a pivot, not a death, and there is a clear continuation.
- **On the discrepancy:** an earlier direct API check returned `archived: false`, `pushed_at:
  2026-06-02`, 295★ / 55 forks. That snapshot predates the 2026-07-01 archival — the star/fork growth
  (295→310, 55→61) confirms the ordering, and the archive banner is unambiguous. So both readings
  were internally correct; they were taken on opposite sides of the archive event.
- **The harness has no licence at all.** No `LICENSE` / `COPYING` file exists in the repo root and
  GitHub's `license` field is `null`. That is **default copyright**: no grant to use, modify,
  redistribute, or take a dependency on the code.

### 10.2 The no-licence fact is a process lesson

An absent licence is exactly the case agenttic's gate exists to catch: an unknown/absent SPDX
classifies as `unknown` and **fails closed in a hosted deployment**
([evaluators/license_gate.py](src/agenttic/evaluators/license_gate.py)). The lesson is procedural —
**run the licence gate on a candidate before spending adapter effort, not after.** A licence check is
a one-line lookup; an adapter is hundreds of lines. Gate first, build second. (HAL's code is off the
table regardless; only its published *ideas* are fair game, below.)

### 10.3 Clean-room boundary

Everything below is drawn only from HAL's **public materials** — the paper (arXiv:2510.11977, ICLR
2026), the README, and the leaderboard site — i.e. its *functionality and ideas*, which are not
protected by copyright. It **must not** be derived from reading HAL's source code (which is in any
case unlicensed). Functionality and design ideas are free to reimplement; only the specific
expression — their code — is protected. What follows is design intent, not their implementation.

### 10.4 Three ideas worth taking

**1. Cost-versus-accuracy as a first-class, certified axis — the strongest idea for agenttic.**
HAL reports results as a **Pareto frontier**: every entry shows accuracy *and* dollar cost
(e.g. "77.8% / $87.16"), and its headline finding is that "agents can be 100× more expensive while
only 1% better," which a "1D leaderboard" hides. For a *certification* product this is more than a
nicer chart: **a certificate that reads "scored X at $Y per task" is much harder to game than a bare
score** — you cannot quietly buy the number with unlimited retries or compute without the cost axis
exposing it. This connects directly to the evaluation-gaming-resistance work already in the repo
(scorer integrity, the ASR self-test).

- **Can the existing shape carry a cost axis? Yes — the data is already collected.** `Scorecard`
  already records `mean_cost_usd`, `total_cost_usd`, `total_scoring_cost_usd`, `p95_latency_ms`, and
  per-run `cost_usd` / `latency_ms` / `steps` ([schema/scorecard.py](src/agenttic/schema/scorecard.py)),
  and the dossier assembles from scorecards, so the numbers already reach the signed artifact. The
  ACP subject rung captures the agent's real token `Usage`, so cost is *measured, not guessed*
  ([docs/ADDING_AN_AGENT.md](docs/ADDING_AN_AGENT.md)).
- **What it would take:** surface cost as a **certified headline axis** ("accuracy-at-cost") rather
  than a buried field, and fold it into the gaming-resistance story — a score achieved at 100× cost
  is a materially weaker claim, and the certificate should say so. Making cost a *reported, signed*
  axis is safe and cheap. Making it a *gate* (failing a cert for poor cost-efficiency) touches the
  promotion gate and is off-limits without care — the hard rule stands (§3).

**2. One interface across many benchmarks — and where it breaks.** HAL ran 9 models × 9 benchmarks
(coding, web, science, customer service) through one harness orchestrating parallel VMs. Two public
lessons: (i) the abstraction hides surprises — they report "higher reasoning effort reducing accuracy
in the majority of runs," a cross-benchmark inconsistency a single number would mask; and (ii)
*maintaining the universal runner was heavy enough to archive* (§7). For agenttic this **validates
not owning a universal runner**: consume each benchmark's upstream runner via the `EvaluatorAdapter`
path, and keep the "one interface" at the *normalization* layer (`EvalResult`) rather than the
*execution* layer (a HAL-style parallel-VM orchestrator). The subject-adapter ladder already embodies
the same "don't write a runner per subject" instinct.

**3. Publish inspectable logs, not just verdicts.** HAL shares **~2.5B tokens of raw agent logs** so
others can *check* results rather than *trust* them, and uses **LLM-aided log inspection** to surface
gaming a score hides (agents "searching for the benchmark on HuggingFace instead of solving a task,"
"misusing credit cards in flight-booking tasks"). Traces are **encrypted before upload to avoid
benchmark contamination.** This is the same ethos as agenttic's "sign the evidence, don't just assert
the verdict," and it maps onto existing machinery: `EvalResult.raw` already keeps each evaluator's
verbatim output, and the per-source reproduction pin (§3, item 5) is the natural place to attach
inspectable evidence. Two specific, borrowable moves: **encrypt-before-publish** (publish logs for
inspection while protecting held-out answers), and **LLM-aided log inspection as a gaming detector**
(a concrete extension of the existing red-team/gaming work).

### 10.5 HAL's published logs as a cheap validation corpus (the §4 question)

HAL publishes its traces as a HuggingFace dataset, `agent-evals/hal_traces` (~113 GB, ~1,700
downloads/month). Validating an agenttic ingestion path against *real published agent runs* — with no
new evaluation spend — is genuinely attractive: it is exactly the kind of cheap, real-data test the
thesis needs. **Two blockers, both to be cleared first:**

1. **Licence unconfirmed.** No licence is shown on the dataset card [unverified — the card's data
   viewer errored on fetch]. Treat it as all-rights-reserved until confirmed.
2. **The traces are encrypted** (HAL encrypts before upload to prevent contamination), so they are
   not directly readable without HAL's decryption path.

Net: a good, cheap test *if* the licence permits and decryption is available — verify both before
writing any ingestion code against it, and (per 10.2) run that licence check first.

---

## Appendix — verified landscape (2026-08-08)

Status/license/output/adapter facts from live GitHub API, PyPI/npm, and official docs on 2026-08-08,
via web research. Not independently re-verified beyond that pass; per-item uncertainty is flagged.

### Eval harnesses & frameworks

| Tool | Status | Agent eval? | Output | License | Ingest difficulty |
|---|---|---|---|---|---|
| **Inspect** (`inspect_ai`, AISI) | Alive, most active (0.3.253, 2026-08-08) | **Yes, first-class** | `.eval` log (Python API) + `inspect log dump` JSON | MIT | **Easy** — best for agent trajectories |
| **lm-evaluation-harness** (EleutherAI) | Alive (v0.4.12, 2026-05-11) | No (single-turn capability) | `results.json` + per-sample JSON | MIT | Easy–moderate (Python API + CLI) |
| **DeepEval** (Confident AI) | Alive, very active (v4.1.5, 2026-07) | Partial (span-level agentic) | Result objects + pytest pass/fail | Apache-2.0 | Easy (pytest-native); judge-cost |
| **promptfoo** | Alive, very active (0.122.0, 2026-08) | Via red-team/providers | JSON + web viewer | MIT | Easy–moderate (Node CLI → JSON) |
| **HELM** (Stanford CRFM) | Alive (v0.5.16, 2026-04) | No | on-disk run artifacts | Apache-2.0 | Moderate (parse run tree) |
| **Ragas** | Alive but stale (v0.4.3, last commit 2026-02-24; org → vibrantlabsai) | Expanding into it | scores dict / DataFrame | Apache-2.0 | Easy; watch maintenance |
| **OpenAI Evals** (`openai/evals`) | Stalled (maintenance-only) | No | JSONL logs | MIT (code) | Moderate, not worth it |

### Observability / eval platforms

| Tool | Status | Output schema | License | Ingest |
|---|---|---|---|---|
| **Phoenix** (Arize) | Alive, very active | **OTel / OpenInference** (standard) | **Split**: server ELv2 (source-available); `phoenix-evals/client/otel` Apache-2.0 | Easiest (standard schema) |
| **Langfuse** | Alive, most-starred (v4.6.0) | Public REST API (traces + scores); OTel ingest | **Open-core** (MIT except `ee/`) | Easy (best-documented API) |
| **MLflow** (GenAI) | Alive, very active (v3.15.1) | `EvaluationResult` + traces via tracking API | Apache-2.0 | Easy (offline/batch) |
| **TruLens** | Alive (2.12.0) | records+feedback in SQLite/Postgres; DataFrames | MIT | Easy (proprietary schema); PyPI marks "Alpha" |
| **LangSmith** | Alive but **proprietary platform** (SDKs MIT) | authenticated proprietary REST | proprietary | Moderate, vendor-gated |
| **OpenLLMetry / OpenInference** | Alive, active | standard OTel GenAI spans | Apache-2.0 | n/a (emitters, not scorers) |
| OTel GenAI semconv | `gen_ai.client` reportedly stable; **`gen_ai.agent` experimental** **[secondary-sourced]** | evolving | — | pin versions; expect churn |

### Agent benchmark suites

| Benchmark | Status | Oracle (how scored) | License | Runner? | Adapter |
|---|---|---|---|---|---|
| **tau2-bench** (Sierra) | Alive (v1.0.1, 2026-07; τ³ in dev) | env-state + action-sequence (deterministic reward; LLM user-sim) | MIT | `tau2` CLI | **Low** |
| **Terminal-Bench / Harbor** | Alive (v2.0, 2025-11) | test-script pass/fail in container | Apache-2.0 | `tb`/harbor | Low–moderate (Harbor is meta-runner) |
| **SWE-bench (+Verified/Lite/Multimodal)** | Alive | **real unit tests** in Docker (resolve-rate) | MIT | Docker harness | Low–moderate; **Verified contaminated / retired as frontier** [secondary] |
| **BFCL** (Berkeley) | Alive, **v4** (2025-07) | AST match + executable | Apache-2.0 | `bfcl` harness (+ Inspect port) | Low–moderate; format-sensitive |
| **GAIA** | Alive (leaderboard) [Space unverified] | exact-match, private test | **gated, license unclear [unverified]** | dataset only | Moderate (bring your own agent) |
| **AgentBench** (THUDM) | Alive (FC variant 2025-10) | per-env state/reward (8 domains) | Apache-2.0 | docker-compose | Moderate (8 heterogeneous envs) |
| **OSWorld** | Alive (Verified 2025-07; V2) | execution state-check (GUI) | Apache-2.0 | run.py / VMs | **High** (real VMs) |
| **WebArena / VisualWebArena** | Stalled (successors: BrowserGym/AgentLab) | functional/state evaluators | Apache-2.0 | run.py + self-hosted stack | **High** |
| **Cybench** | Alive | flag capture (deterministic) + subtasks | Apache-2.0 | Docker harness | Low–moderate; dual-use |
| **MLE-bench** (OpenAI) | Alive (through 2026-02) | Kaggle metric → medals | LICENSE present, **SPDX unverified** | `mlebench grade` | Moderate (Kaggle auth, GPU) |
| **SWE-Lancer** (OpenAI) | Alive, **moved** to `preparedness` | e2e tests, $-weighted | MIT | Docker | Moderate |
| **SWE-bench Pro** (Scale) | Alive | tests; **GPL OSS split**, held-out/commercial not self-runnable | GPL (OSS split) | `-os` harness | Public: moderate; rest: vendor-gated |
| **GDPval** (OpenAI) | Alive (2025) | **human/LLM pairwise win-rate** (not execution) | gold subset, **license unverified** | grader service | **High** (no deterministic oracle) |
| **HAL** (Princeton) | **Harness repo archived 2026-07-01** (read-only); **project alive, pivoted to reliability** | 9 benchmarks, **accuracy + cost (Pareto)**, ~2.5B tokens of published logs | **none — no LICENSE, license field `null` (default copyright)** | archived harness | see §10 — borrow the design, not the code |

**Explicitly unverified:** OpenAI's SWE-bench-Verified retirement post (403; secondary sources only);
GAIA dataset license and leaderboard liveness (Space did not fully load); MLE-bench and GDPval exact
SPDX; per-span stable/experimental markers in the OTel GenAI semconv (secondary write-ups); Ragas's
exact latest version (org move + ~2026-02-24 last commit confirmed); the `agent-evals/hal_traces`
dataset license (card viewer errored — no license shown). The three research passes were one-hop web
research; they were not independently re-verified beyond the 2026-08-08 pass.

**Corrected since first draft:** the first draft claimed HAL "was archived 2026-07-01 with no
successor" and used it as evidence that the meta-aggregator concept had died. That conclusion was
wrong. Verified 2026-08-08 (repo page, GitHub API, leaderboard site): the *harness repo* is archived
and unlicensed, but the *project* is alive and pivoted to reliability. Full correction and the
clean-room design assessment are in §10; the case-against (§8.4) and maintenance (§7) sections were
rewritten accordingly rather than footnoted.
