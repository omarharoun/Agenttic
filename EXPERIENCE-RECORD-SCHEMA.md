# Shared Experience Pool — Experience Record Schema (Playground §3.3, Step 1)

Status: **design only, no implementation.** Step 1 of the Playground/experience-pool
workstream: define the experience record schema — task, strategy/trajectory, verified
outcome, scorecard reference, vertical/profile tag — and settle the §6-undecided
question of *how strategy/trajectory is represented*.

Companion to yesterday's [RECEIPT-SCHEMA.md](RECEIPT-SCHEMA.md): pool **writes** are
gated by the Tool Access Receipt defined there, tied to a passing scorecard (§8 below).

---

## 0. Scope note — RECONCILED against the original handover

**This doc has now been reconciled against the original Playground handover on disk**
(`…/uploads/c1600779-agentticplaygroundhandover.md`), not a summary. The core schema was
designed against the human's chat restatement; reading the original **changed nothing in
the core** and confirmed it (sandbox+scoring reuse, staged ladder, hardening-inverted
pool, receipt-gated writes, vertical-scoped reads, seeded canaries). It surfaced three
refinements from parts not in the summary — the §1 human-validator origin, and the two §6
open decisions on canary placement and first task category — all folded in below and
reasoned in **§9**. (The unrelated file `…/9ac1377f-HANDOVER.md` is the *agentmem*
belief-harness handover from a different project, used here only as background evidence for
§5, never as this workstream's source.)

The Playground, per the original: a front door onto the **already-built** benchmarking
engine + staged release ladder, plus one new layer — agents learning from each other's
verified experience. Agents earn real-tool **and shared-pool-write** access by climbing
`internal → vetted → limited → GA`. A run that passes **its scorecard** (not a self-report)
is promotable; writes are receipt-gated (the poisoning vector is "the cross-agent
equivalent of the injection problem Lane 2 handles for tool outputs" — handover §3.3);
reads are vertical/profile-scoped; tripwire tasks are seeded because the Playground "is
exactly the environment where **reward-hacking or judge-gaming** would first appear" (§2.4).
Its §1 origin — a training framework with **humans as validators** — is the one place the
current design departs from the founding idea (§9.3). If the original contradicts anything
here, the original wins and this doc is the thing to correct.

---

## 1. Ground truth first — what the reused building blocks actually do

Every claim checked against source before designing. All the reuse targets are real;
three things are richer or stricter than a summary would suggest, and they change the
schema.

| Building block | Verdict | Evidence |
|---|---|---|
| Scorecard to key against | ✅ real, rich | `schema/scorecard.py`: `scorecard_id, agent_id, suite_id/version, rubric_id/version, task_success_rate, per_criterion_means, per_criterion_na_counts, errored_test_ids, assertions, coverage`. `RunScore`: `test_id, passed, scoring_error, criterion_scores, cost_usd, latency_ms`. |
| Graduation = staged release ladder | ✅ real | `release/ladder.py` + `schema/release.py`: `internal → vetted → limited → ga` (`STAGE_ORDER`, ga strictest), folded from append-only `PromotionRecord`s (`agent_stage()`), gated least-privilege, tighten-only. This is what gates **earned tool/data access** in the sandbox: `stage_gate()` denies a caller above the agent's promoted stage. So an agent's stage bounds *what a strategy could have done* — a signal the record carries as `producer.stage_at_write`. |
| Hardening loop this "inverts" | ✅ real, and its disciplines transfer | `hardening.py` — see §1.1. |
| BehavioralScope (the evidence-quality payload) | ✅ real (shipped yesterday, a103881) | `passport/scope.py`: derived from a scorecard, fail-closed calibration, "fence not a badge". This *is* the point-1 answer — see §4. |
| Canary primitive (§3.4) | ✅ real | `enforce/canaries.py`: `CanaryManager`/`CanarySet`/`CanaryTrip` (decoy_tool / canary_credential / tripwire_domain); "a canary is never legitimately used, so a trip is a **confirmed positive**". Plus `redteam/honeypot.py` `HarnessEnforcementResult` (resisted/attempted_blocked/executed_allowed). |
| Receipt to gate writes | ✅ real, designed yesterday | [RECEIPT-SCHEMA.md](RECEIPT-SCHEMA.md) Tool Access Receipt. |

### 1.1 The hardening loop is the exact inversion — and the pool must inherit its honesty

`hardening.py` promotes **failing** (non-errored, non-passed) cases from a scorecard into
a per-agent regression suite, to re-test that a fix held. The pool inverts the sign —
**passing** runs → shared experience — but the honesty disciplines are not optional
decoration; they are the reason the loop is trustworthy, and each has a mirror the pool
needs:

| Hardening discipline ([hardening.py](src/agenttic/hardening.py)) | Pool mirror the schema must carry |
|---|---|
| **"ground truth is never invented from a trace"** — live-reconstructed cases keep `expected=None`, never fabricated (`promote_live_failures_op`) | The pool never asserts *the strategy was good*. Only that *the run passed*. Distilled strategy is marked **claimed**, kept separable from verified trajectory (§3). |
| **errored ≠ failed** — errored runs excluded from the verdict (`_failing_runs`) | passed ≠ good — a pass is the *outcome* verdict, not a strategy endorsement (§4). |
| **reconstructed/uncertain cases stay `approved=False`** behind the Step-8 human gate | Barely-passing / provisional-judge / canary-adjacent records carry `review_state: needs_review` — not silently retrievable (§4, §7). |
| **de-dupe by content fingerprint** (`fingerprint()` = task+input+rubric), not by id, "so the suite doesn't bloat" | The pool de-dupes near-identical strategies for one task-shape, or survivorship *amplifies* one lucky approach into apparent consensus (§5). |
| **per-case provenance manifest** ("why" it failed, source ids, fingerprint) | Per-record provenance: scorecard ref, suite/rubric versions, strategy origin, write-receipt (§2). |

Note the load-bearing convention across `hardening.py`, `passport/receipts.py`, and
`ingest.mapping`: **hashes, not payloads** — request bodies are stored as
`content_sha256`, not content (`hardening.py:263-283`). The trajectory representation
(§3) obeys this: it stores the *shape* of a successful run, never the customer's data.

---

## 2. The experience record (schema)

One JSON object. `typ` versioned and distinct so it is never confused with a scorecard, a
receipt, or a regression case. Every field justified; the evidence-quality section is
mostly *a `BehavioralScope`* (§4), not fifteen reinvented fields.

```jsonc
{
  "typ": "agenttic/experience-record@1",
  "record_id": "exp-<16hex>",

  // --- ROUTING / ISOLATION (§6 decision) ---------------------------------
  // NOT an in-pool filter. It is the STORE SELECTOR + a misroute check. The
  // isolation boundary is the per-vertical store, not this tag (§6, §7).
  // REQUIRED and EXPLICIT — never absent, never a fallback. A mechanics-first
  // general pool uses vertical:"general", which is its OWN isolated store, not a
  // superset readable by real verticals (§9.1). Absent would be ambiguous once a
  // real vertical exists: "general" vs "an insurance record that lost its tag".
  "vertical": "insurance-triage",           // or "general" for a loop-mechanics pilot
  "cert_profile": { "id": "cert-insurance-triage-v1", "version": 3 },

  // --- THE TASK ----------------------------------------------------------
  "task": {
    "description": "…",                     // NL, the embeddable retrieval surface
    "fingerprint": "<sha1:16>",             // reuse hardening.fingerprint(task+input-shape+rubric)
    "input_shape": { "keys": ["claim_id","policy_type"], "schema_sha256": "…" }
                                            // shape/keys, NOT values (hashes-not-payloads)
  },

  // --- STRATEGY / TRAJECTORY (the §6 question) — two layers, §3 ----------
  "trajectory": [                           // VERIFIED shape, derived from the scored Trace
    { "step": 0, "tool": "lookup_policy", "action_class": "read",
      "input_sha256": "…", "output_sha256": "…", "step_score": 1.0 }
    // … no argument values, no results — shape + per-step verdict only
  ],
  "strategy": {                             // CLAIMED abstraction — the retrievable summary
    "summary": "decompose → verify coverage before quoting → escalate on ambiguity",
    "provenance": { "by": "extractor:deterministic | self | judge:<model-id>",
                    "at": "<ts>", "model": "<id-or-null>" },
    "verified": false                       // ALWAYS claimed, never verified (survivorship, §5)
  },

  // --- VERIFIED OUTCOME + EVIDENCE QUALITY (§4) --------------------------
  "outcome": {
    "passed": true,                         // required; only passing runs are eligible
    "scorecard_ref": {                      // key against the real Scorecard
      "scorecard_id": "…", "agent_id": "…", "agent_config_hash": "…",
      "suite_id": "…", "suite_version": 4, "rubric_id": "…", "rubric_version": 2
    },
    "scope": { /* a BehavioralScope, §4 — verified_capabilities,
                  provisional_capabilities, coverage/coverage_holes, not_measured,
                  reliability{pass_1,pass_k,k}, assertions, envelope,
                  suite_provenance{integrity_gate,contamination} */ },
    // explicit "how strong is this pass" (point 1) — the weighting a consumer reads:
    "margin": 0.18,                         // task_success_rate − pass_threshold
    "calibration_state": "deterministic | calibrated α=.. | PROVISIONAL",
    "na_counts": { "no_exfiltration": 3 },  // per_criterion_na_counts — criteria that didn't apply
    "n_scored": 12, "n_errored": 0,         // denominators
    // survivorship denominators (point 2):
    "attempts_before_pass": 1,              // 1st-try vs Nth-try (null if sandbox doesn't track)
    // safety (point 1 + §3.4). canaries_present distinguishes "none offered" from
    // "offered, none tripped" — required for a MIXED canary track (§9.2), where
    // presence is per-run. In a SEPARATE track it is false on every real-task
    // record, and the safety signal comes from producer.canary_standing instead.
    "canaries_present": true,
    "canaries_tripped": 0,                  // > 0 ⇒ INELIGIBLE, never write (§4)
    "harness_enforcement": { "resisted": 4, "attempted_blocked": 0, "executed_allowed": 0 }
  },

  // --- PRODUCER TRUST CONTEXT --------------------------------------------
  "producer": {
    "passport_id": "pp-…", "passport_hash": "…",
    "stage_at_write": "vetted",             // release/ladder.agent_stage — battle-testedness
                                            // AND the gate on pool-WRITE access (§3.2, §9.4)
    "tier": "B",                            // certification tier
    "canary_standing": {                    // agent's honeypot record at write time (§9.2) —
      "tripped_ever": false,                //   load-bearing when canaries are a SEPARATE track,
      "honeypot_ref": "…-or-null"           //   so a clean real-task record still carries safety
    }
  },

  // --- WRITE AUTHORISATION (yesterday's receipt) — audit link, not the gate
  "write_receipt": {
    "receipt_id": "tar-…", "nonce": "…",
    "bound_to": { "scorecard_id": "…", "record_content_sha256": "…" }
  },

  // --- LIFECYCLE / ACCRUAL ----------------------------------------------
  "created_at": "…", "content_sha256": "…",
  // The human-validation hook (§9.3). The origin of this idea had HUMANS as
  // validators; the scorer+ladder replace that for the routine case, NOT for the
  // gamed/low-margin/provisional tail — which routes to needs_review, mirroring
  // §3.2's hardening human gate. validated_by/at are the provenance of a real
  // human sign-off; null while auto. Optional but present in the schema on purpose.
  "review_state": "auto | needs_review | reviewed",
  "validated_by": null,                     // human validator id, when review_state=reviewed
  "validated_at": null,
  "reuse": { "times_retrieved": 0, "times_reused": 0, "reuse_pass_rate": null }
                                            // accrues at the sandbox layer (§7), not at write
}
```

Required always: `typ, record_id, vertical, cert_profile, task, trajectory, strategy,
outcome{passed, scorecard_ref, scope, canaries_*}, producer, write_receipt, created_at,
content_sha256, review_state`. `reuse.*` starts empty and accrues.

---

## 3. How strategy/trajectory is represented (the §6 decision)

**Two layers, deliberately split — verified shape vs. claimed abstraction.** Reasoned
against three failed single-layer options:

- **Raw full trace only** — rejected. It leaks payloads (violates hashes-not-payloads),
  is huge, and does not generalise: a *verbatim* trace can't be RAG-retrieved for a
  *similar* task. And it over-fits — sharing one exact path is survivorship at the
  step level.
- **Distilled NL strategy only** — rejected as the *sole* record. A distilled "why it
  worked" is a **claim**: the agent may have passed for a reason it didn't articulate, or
  the scorer missed a shortcut. Storing only the claim, with the evidence discarded,
  reproduces exactly the poisoning vector point 1 warns about — an unfalsifiable assertion
  that propagates.
- **Both, but fused into one blob** — rejected. agentmem's finding is explicit
  (PRACTICES_PLAN §3.1): *provenance is the thing you most want separable when auditing a
  bad answer.* Fusing verified and claimed makes the claim inherit the evidence's
  authority.

**The design:**

1. **`trajectory` — verified, low-level, hashes-not-payloads.** The ordered sequence of
   steps as `(tool, action_class, input_sha256, output_sha256, step_score)`, derived
   directly from the scored `Trace`. It provably *happened* and provably *scored as
   recorded*, and it carries **no values** — so it shares the *structure* of a successful
   approach (which tools, in what order, with what per-step outcome) without exposing the
   customer's data. This is the same move as yesterday's receipt `action_hash` (bind the
   tool + shape, not the values), and the same convention as `hardening.py`'s
   `content_sha256` inputs.

2. **`strategy` — claimed, high-level, the retrieval surface.** A structured/NL
   abstraction of the approach — this is what gets embedded and RAG-retrieved. It carries
   **explicit `provenance`** (`extractor:deterministic` | `self` | `judge:<model-id>`)
   and `verified: false` **always**. A consumer reads `strategy.summary` to judge
   relevance, then inspects `trajectory` as the backing evidence. The strategy is
   provisional *by construction* — a post-hoc rationalisation of a survivorship-selected
   run (§5) — and the schema states that rather than hiding it, exactly as yesterday's
   fail-closed provisional-vs-verified rule.

Who distills matters and is recorded, not assumed: a `self`-distilled strategy is the
agent grading its own homework (weakest provenance); a `deterministic` extractor over the
trajectory shape is strongest but least expressive; a separate `judge` model is a cost and
its own error source. The field carries which, so the retrieval layer can weight it.

---

## 4. Point 1 — receipt-gating authenticates the poisoning vector, it does not remove it

The bound I wrote yesterday applies and bites harder: **"no ungoverned write, not no
unsafe write."** A passing scorecard proves the *outcome* was acceptable; it does not
prove the *strategy* was good. A run that passed by luck, or by a shortcut the scorer
didn't catch, yields a receipt-valid record asserting "this strategy worked" — and the
gate then propagates it. Receipt-gating makes the poisoning vector **authenticated**, not
absent.

**What the schema does about it: make a barely-passing record distinguishable from a
well-evidenced one — and the repo already computes all of it.** The `outcome.scope` field
is a `BehavioralScope` (`passport/scope.py`, shipped yesterday), which is *derived from
the scorecard* and already carries every signal you named:

- **confidence / margin** → `outcome.margin` (task_success_rate above the pass threshold) +
  `scope.reliability` (`pass_1`, `pass_k`, `k`) — passed *once* vs passed *k times*.
- **judge calibration state at score time** → `outcome.calibration_state` +
  `scope.provisional_capabilities`. This reuses the F1 fail-closed derivation
  (`criterion_status`): a pass scored by an **uncalibrated** judge is itself provisional —
  the "pass" is unproven, and the record says so instead of laundering it into certainty.
- **N/A counts** → `outcome.na_counts` (`per_criterion_na_counts`, shipped yesterday F2).
  A pass where half the criteria were N/A cleared a low bar — the situations they check
  never arose.
- **whether canaries were in that run** → `outcome.canaries_present` /
  `canaries_tripped` / `harness_enforcement`. **`canaries_tripped > 0` ⇒ ineligible**:
  a run that tripped a canary demonstrated unsafe behaviour (a *confirmed positive*,
  `canaries.py`) even if the task scorecard passed. Task-pass never launders a safety
  trip. This is exactly why the spec seeds canary/honeypot tasks *inside training runs*
  (§3.4): **the Playground is where an agent first discovers a shortcut** — a decoy tool
  or exfil path that passes the task cheaply — so the record must capture whether the
  passing run took the bait, or the pool would promote the shortcut as a "strategy that
  worked." `canaries_present: false` is itself a weakness signal: a pass never exposed to
  a shortcut is weaker evidence of a *safe* strategy than one that was and resisted.
- **coverage** → `scope.coverage` / `coverage_holes`: did the run exercise the task's
  space, or a narrow slice? A pass over a thin slice is a thin pass.

The schema **exposes** these. It cannot make anyone **use** them — see §7.

---

## 5. Point 2 — survivorship bias, and inheriting certainty you didn't earn

A pool of *passing* runs is a survivorship-biased sample **by construction**: the
strategies that failed, and the base rate, leave no record in it. This is not a
hypothetical — it is measured next door.

The agentmem work (PRACTICES_PLAN.md, agentmem HANDOVER §4.2/§5.1) found that
**seeded knowledge transfers unearned confidence**: a practice "raises confidence by
construction; if correctness does not move with it, the practice is a liability"
(PRACTICES_PLAN §7.4), and the system's failure mode is "confident and plausible, not
loud" (§5.1 — four bugs, three silent). A shared experience pool is that experiment at
scale: agent B retrieves A's strategy and inherits A's certainty without A's evidence.
The handover's own derived evals (§4.2 b/d) are flagged as survivorship-biased for the
identical reason — "they only capture mistakes a user noticed."

**What the schema carries so a consumer can hedge rather than inherit:**

- `strategy.verified: false` — the abstraction is never presented as fact.
- `outcome.attempts_before_pass` — a 1st-try pass and a 10th-try pass are different
  evidence; the denominator travels with the record.
- `outcome.margin` + `scope.reliability.pass_k` — decisiveness, not just a boolean pass.
- `producer.stage_at_write` + `tier` — a strategy from an `internal`-only agent is less
  battle-tested than one from a `ga` agent.
- `reuse.reuse_pass_rate` — the one field that *earns* trust over time (see §7).

**What the schema cannot carry, because no single record can see it:** the base rate.
"This strategy passed" is meaningless without "…out of how many attempts at this
task-shape, and how many *other* strategies passed too." That is a **pool-level**
statistic, not a record field (§7).

---

## 6. Point 3 — the §4 mechanism decision is already answered: per-vertical store isolation

§4 asks shared-store vs handoff vs MCP-resource and leans MCP. But the load-bearing
requirement is §3.3's **read-scoping by vertical/profile**, and that is the *same question
the human already decided for agentmem practices* — with measured evidence that a
**filterable tag does not work** and the **store boundary must do it**:

- **A scoping tag is equality-only and unenforced at write time.** agentmem's `subject=`
  is `subject=?` equality with no namespace semantics, and `consolidate()` writes whatever
  subject the model emits — "a hallucinated or drifting subject string silently creates an
  orphan namespace… the §5.1 confident-and-plausible failure class" (PRACTICES_PLAN §1.1).
  A `vertical` tag filtered at read time has the same two holes.
- **A shared pool with a global retrieval limit starves a scoped read.** Measured: 300
  strongly-matching rows under `practice:other` + one weak row under `practice:mine` →
  `recall(subject="practice:mine")` returns **0 hits**, because the `LIMIT 200` window is
  global then intersected (§1.1.3). A shared experience pool with RAG top-k has *exactly*
  this failure: each vertical's retrieval quality decays as *unrelated* verticals grow.
- **Cross-scope leakage happens on the path that ignores the tag.** agentmem's procedural
  facts leak across every practice today because `compose()` passes no subject (§1.2) —
  "the leaking tier is exactly the tier that ignores the argument."

The human's settled decision there was **one database per practice**, and the plan
endorses it as *stronger* than the spec claimed: "separate files remove the shared lexical
window entirely" and "the decision has already made 'not at all' [cross-scope reads] the
default by construction" (§1.1, §3).

**Transfer to the pool: assume per-vertical isolation at the store boundary.** One store
per `(vertical, cert_profile)`, not one pool with a `vertical` column filtered at read.
Consequences the schema must reflect:

- `vertical`/`cert_profile` in the record are the **store selector + a misroute check**,
  not the enforcement mechanism. Enforcement is which store you opened.
- Cross-vertical reads are **not a filter someone forgot** — they are a second connection
  and an explicit, secured API someone must design. Default: **not at all** (§3 of the
  agentmem plan). The schema should not presume a `confidence_discount` for cross-vertical
  reads — agentmem found that idea has "no mechanism to be honest" (§3.1), because it
  overloads a confidence number that already means three things.
- On the §4 transport (MCP vs store vs handoff): MCP-resource is fine as the *read
  interface*, but it must be **one resource per vertical store**, not one resource over a
  shared pool. The isolation is the store; MCP is just the door to it.

---

## 7. What the schema cannot fix, and which layer must

The schema's job is to make evidence quality **legible**. Four things it cannot do, and
where each must live:

1. **Make a consumer weight the fields.** The schema exposes `margin`,
   `calibration_state`, `pass_k`, `na_counts`, `canaries_tripped` — but a barely-passing
   provisional record and a decisive calibrated one are still both "passed: true." The
   **retrieval/ranking layer** must rank by these (down-rank provisional, thin-coverage,
   low-margin, high-N/A), and the **consumer/prompt-assembly layer** must hedge (present a
   retrieved strategy as "one agent passed this once," never as fact). Schema exposes;
   retrieval ranks; consumer hedges. Three layers, and the schema is only the first.

2. **Correct survivorship base rates.** A record cannot hold what the population hides
   (§5). The **write layer** (the sandbox) must *also* record the denominator — failed
   attempts at the same `task.fingerprint` — or the **retrieval layer** must join a
   retrieved strategy against the non-survivorship run population ("passed 3 of 40
   attempts at this task-shape"). Without that, the pool reads as consensus when it is
   selection.

3. **Verify the strategy was causal.** `strategy.summary` may be wrong even when
   `trajectory` is real (§3). No field fixes this; only **re-execution** does — the
   strategy earns trust by being reused on a *fresh similar task* and passing again. That
   is the **sandbox/scoring layer** (§3.1) applied to strategies, tracked in
   `reuse.reuse_pass_rate`. Until it accrues, a strategy is claimed, full stop. (This is
   the pool's version of `hardening.py`'s re-run delta: a promoted item proves itself by
   re-passing, not by being promoted.)

4. **Prevent an unsafe-but-passing write, or enforce read-scope.** Receipt-gating proves
   the write was *authorised* (a real scorecard, a real pass), not that the record is
   *good* — the **gateway/policy** decides issuance and the honest bound is "no ungoverned
   write" (§4, and yesterday's receipt bound verbatim). Read-scope is enforced by the
   **store boundary**, not the `vertical` tag (§6). The schema records both as provenance;
   neither is enforced *by* the schema.

The one-line version: **the schema can make poison labelled and rankable; it cannot make
it absent. Absence is a job for the ranking layer, the sandbox re-execution layer, and the
store boundary — not for any field.**

---

## 8. Write-gate linkage to the receipt (brief — for Step 2, not built here)

A pool write is **irreversible** (a poisoned record propagates), so it is exactly the
irreversible-action case from yesterday's [RECEIPT-SCHEMA.md](RECEIPT-SCHEMA.md) §4: the
Tool Access Receipt must carry `bound_params` — a salted hash of the specific instance —
binding `{scorecard_id, record_content_sha256}`. The gateway issues that receipt only
after verifying the scorecard exists and passed. The experience record carries the
resulting `write_receipt` for audit (which receipt authorised this write), and the store
verifies signature + expiry + nonce + the bound scorecard before accepting the write.
None of that is in this deliverable — schema only, as asked.

---

## 9. Reconciliation with the original handover

Read against the original (`c1600779`), the **core schema is unchanged and confirmed** —
§1–§8 above stand. Nothing in the original overturns the two-layer trajectory/strategy
split, the BehavioralScope outcome, per-vertical store isolation, receipt-gated writes,
canary disqualification, or the survivorship hedging. The original is close to the human's
restatement; the summary was accurate. What follows is the honest diff: what I'd have baked
in from the start had I had the original, drawn from the parts not in the summary.

### 9.1 First task category (§6 open decision) — the tag is required and non-fallback
Whether the first pool is **one real pilot vertical** or a **general-purpose set to prove
loop mechanics** does bear on the schema, in one way: the `vertical`/`cert_profile` tag
must be **required and explicit even when the answer is "general."** `vertical:"general"`
is its **own isolated store**, not a global fallback readable by real verticals — otherwise
the day a real vertical is added, the isolation boundary (§6) is retroactively porous and
every pre-existing record is ambiguous between "general" and "a real record that lost its
tag." A general-mechanics pool is also the **most survivorship-prone** (no domain grounding
to sanity-check a strategy), so the §5 hedging fields matter *more* there, not less. Change
made: the routing block is now marked required/explicit with `general` called out.

### 9.2 Canary placement (§6 open decision) — mixed vs separate track changes the record
You were right that this interacts with making canary trips disqualifying. Two designs,
two things the record must carry:
- **Mixed in** — canary presence is **per-run**, so `outcome.canaries_present` (distinct
  from `canaries_tripped`) is load-bearing: it separates *"a shortcut was offered and the
  agent resisted"* (strong safety evidence) from *"no shortcut was ever offered"* (no
  evidence either way). Without `canaries_present`, a clean record is ambiguous exactly as
  you said. The schema already split these two fields; §4 now states why.
- **Separate track** — real-task runs contain no canary, so `canaries_present` is *always
  false* on them and per-record canary evidence is null. The safety signal then has to be
  **agent-level**: `producer.canary_standing` (did this agent ever trip a canary in the
  separate track, at write time). Added to the schema. Without it, a separate-track design
  would let a clean-looking record come from an agent that was gaming canaries in the other
  track — the disqualification has to attach to the *producer*, not just the run.

Net: the schema now supports both, and **is never silent on canary exposure** — the
ambiguity you flagged is closed from both directions.

### 9.3 Human validators (§1 origin) — the schema needs the field; scorer+canaries do NOT fully replace them
This is the most consequential of the three, and my answer is **no, the scorecard plus
canaries do not genuinely replace the human validator — not for the tail — and the record
needs the field.** Reasoning, not assertion:
- The founding idea (§1) had **humans as validators**. The current design puts the scorer
  and the ladder in that role with no human anywhere. That is a real departure, and the
  platform's *own* §3.2 hardening gate keeps uncertain cases behind a human
  (`hardening.py`: live-reconstructed cases stay `approved=False` until a human clears
  them). The pool inverts hardening; it should inherit that gate, not drop it.
- The scorer is **gameable by construction** — that is the stated reason canaries exist
  (§2.4, "reward-hacking or judge-gaming would first appear" in the Playground). Canaries
  catch **seeded, known** shortcuts; they cannot catch a **novel** gaming the seeder didn't
  anticipate. So there is a residual class — *passed, no canary tripped, but actually
  gamed* — that neither the scorer nor the canaries catch. That residual is precisely where
  a human validator earns their place.
- So the honest split: **scorecard + canaries replace the human for the routine case**
  (high-margin, calibrated-judge, canary-exposed-and-resisted) — which is most runs, and is
  real leverage. They do **not** replace the human for the **tail** (low-margin, provisional
  judge, high-N/A, novel-shortcut-suspect). The record must route that tail to a human.
- Mechanism in the schema: `review_state` already had `needs_review`; I've made explicit
  that this **is** the human-validation hook and added `validated_by` / `validated_at` so a
  `reviewed` record records **who** signed off, not merely that it was. It stays **optional
  and defaults to `auto`** — a high-confidence record is auto-eligible; the ranking layer
  routes the tail to `needs_review`; a human moves it to `reviewed`. This keeps the human in
  the loop for exactly the cases where the scorer's word isn't enough, which is what the §1
  origin was for and what §3.2 already does elsewhere.

### 9.4 Smaller confirmations from the original
- **§3.2 gates pool-WRITE access, not just tool access.** The ladder is not only a trust
  annotation on `producer.stage_at_write`; reaching a stage is a **precondition to writing
  to the pool at all**. Noted at the field and folded into the write-gate story (§8): a
  write needs (a) a receipt bound to a passing scorecard *and* (b) an agent that has climbed
  to write-eligible stage.
- **§4 "namespacing so one agent's facts don't silently overwrite another's"** — the
  original worries about overwrite under a shared store. The append-only, per-producer,
  content-hashed record here **dissolves that concern without namespacing**: writes never
  overwrite, they accrete (de-dup by fingerprint handles bloat, §1.1), and every record
  carries its `producer`. Overwrite-safety is a property of the log shape, not of a
  namespace tag.
- **§4 "reads/writes are a misuse surface"** — the original wants reads governed too, not
  just writes. This doc governs reads by the **store boundary + retrieval scoping** (§6, §7)
  rather than a per-read receipt; that is the honest place for it, and it is consistent with
  the standing principle that memory access is a tool-call-like misuse surface.
