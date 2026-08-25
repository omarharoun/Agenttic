# SPEC-13 — Coverage-Driven Agent Verification (build record)

This spec changed the *method*, not the machinery. Everything before it made
Agenttic the best implementation of the industry's approach — a fixed suite, run,
scored, reported as a pass rate. Hardware verification abandoned that method in
the 1990s, because the question that decides tape-out is not *"what passed?"* but
***"what did we never exercise?"***

| Industry practice | What shipped here |
| --- | --- |
| A fixed suite of test cases | Constrained-random stimulus, generated fresh every run (M42) |
| Pass rate as the headline | Functional coverage closure as the headline (M41, M44) |
| Score the final outcome | Assertions monitored continuously on every trace (M40) |
| Sample the safety question | Formal proof over the tool-authorization layer (M43) |
| Score only what the agent *did* | Check what it *said* about the same policy (M45–M46) |
| "86% passed" | Sign-off: closure + assertions + properties + bug curve (M44) |

## Milestones

| Milestone | Step | State | Tests |
| --- | --- | --- | --- |
| M40 — Assertions | 62 | ✅ | 49 |
| M41 — Coverage model | 59 | ✅ | 18 |
| M42 — Stimulus + CDV loop | 60–61 | ✅ | 40 |
| M43 — Formal (authorization layer) | 63 | ✅ | 18 |
| M44 — Sign-off + vPlan | 64 | ✅ | 14 |
| M45 — Formal (claim layer) | 63b | ✅ | 18 |
| M46 — Claim leg: soundness, sign-off, extraction | 63b–d | ✅ | 31 |

## The load-bearing ideas

1. **Vacuity (M40).** An assertion whose antecedent never occurred reports
   `unexercised`, never `pass`. Returning `pass` there would make the suite look
   clean while proving nothing.
2. **Two coverage numbers (M41).** *Stimulus* coverage is what was requested;
   *trace* coverage is what the run exhibited. Closure is computed on the trace
   side. One number would let a generator claim corners it never reached.
3. **The two-stage split (M42).** The solver is pure seeded code that cannot
   import a model client; only realization touches a model. An LLM inside the
   sampler destroys reproducibility, distribution control and hole-targeting at
   once.
4. **The derived oracle (M42).** The abstract point plus the policy *is* the
   reference model. Asking a model "what should the agent do here?" is the trap
   the spec exists to avoid.
5. **Constraint propagation (M42, not in the spec).** Pinning a bin is not enough
   to reach a corner that exists only as a rare conjunction — the implications
   must be propagated first, or hole-targeting silently degrades to random.
6. **Four-valued proofs (M43).** Exhaustive reachability over a *finite* guard
   layer is a decision procedure and yields `proven`. A **bounded** check never
   does. Unbounded domains, exhausted exploration caps and missing solvers each
   report themselves.
7. **The untested line (M44).** Requirements with nothing mapped to them are
   flagged loudly. No eval tool can produce that line without a declared model of
   what "tested" means.
8. **Actions and words are two questions (M45).** An agent can stay entirely
   inside its authorized tools and still tell a customer "you don't need approval
   for that" when the policy requires it. Nothing in the action graph is wrong;
   the lie is in the sentence. `proof(authorization)` is four-valued and
   `proof(claim)` is five-valued, and they never share a report row — a reader
   seeing `1 counterexample · 1 invalid` on one line cannot tell whether the
   agent *did* something illegal or *said* something false, and those route to
   different owners.
9. **Agreement is the confidence signal (M45).** Translation from prose onto
   guard-layer variables is done by a model and is provisional. Rather than
   trust one opinion, the extractor is sampled `n` times and only mappings every
   run produces are sent to the solver; the rest are AMBIGUOUS. This only works
   if the extractor makes a fresh call per invocation — a memoized one returns
   one opinion `n` times and manufactures unanimity out of it.
10. **A bounded search must report that it was bounded (M46).** `check_claim`
    answered "is this tool permitted" by counting how many *reachable* states
    enable it, against a capped search. Comparing `enabled == len(states)` on a
    truncated set is self-referential — both sides shrink together — so
    truncation could never make the test fail, only make it confident. It
    reported VALID over one third of the state space. `prove` had this right
    from the start (`unbounded`, never `proven`, at the cap); the claim layer
    now returns AMBIGUOUS.
11. **"Not checked" needs its own slot (M46).** If a failed extraction collapses
    into "checked and found nothing", the denominator quietly shrinks and a clean
    report becomes unfalsifiable. `extraction_failures` is counted outside the
    five buckets and printed above them, the same shape as `unexercised`,
    `not_measurable`, `non_results` and `evaluation_failures`.

## Hard rules added (56–63)

56. Coverage closure, not pass rate, is the headline; a pass rate with no
    coverage model is labelled unscoped.
57. Every generated scenario is reproducible from its seed plus the space
    version; the realized scenario is stored verbatim.
58. Expected outcomes are derived from the abstract point and the policy
    document, never guessed after the run.
59. Assertions run on every trace — batch and live. A violation is a failure
    regardless of scores.
60. Unexercised assertions are reported as unexercised, never as passed.
61. Unhit bins are always reported; waiving one requires a named reason.
62. Formal claims state their scope — the guard layer, not the model — in the
    same sentence as the claim.
63. Failing generated scenarios become directed regression tests through the
    normal human gate.

## Hard rules added (72)

72. `proof(claim)` and `proof(authorization)` never share a report row. They ask
    different questions of the same guard layer, and merging them makes the
    answer unreadable.

## Honest notes

- **The spec's own discipline check stands.** None of this makes a claim
  *proven*; SPEC-8's real-model run and human judge calibration still gate every
  claim. That is why the sign-off carries a provenance leg naming the calibration
  state of every judge and classifier, and why classifier-backed coverage bins
  render PROVISIONAL until measured.
- **z3 is an optional extra** (`agenttic[formal]`). Exhaustive reachability over
  the finite guard layer needs no solver; the z3 path is a *bounded* check that
  can refute but never proves, and without z3 it reports `not_attempted` rather
  than assuming safety.
- **Not built, per §9 of the handoff:** multi-agent coverage, coverage over model
  internals, formal verification of anything beyond the authorization guard
  layer, a UI, and external benchmark imports.
- **Claim checking is OPT-IN and says so.** `verify_op` runs on the normal path
  for every run and promises zero model calls; claim extraction needs a model to
  read the agent's prose, so it runs only when a caller supplies BOTH an
  extractor and the policy to check against. With neither, the leg reads
  `not_run` — which is not the same as "no false claims found", and the report
  renders it as `not checked`. A network-block test enforces the promise.
- **The claims leg is REPORT-ONLY under gate v1**, following the scoreboard
  precedent: computed, rolled up and rendered, and it does not gate. A verified
  false claim does not currently block sign-off. Flipping that is a separate,
  announced change under a `gate_version` bump, so a sign-off issued under v1
  keeps meaning what it meant when it was issued.
- **The checkable vocabulary is narrower than "did the agent tell the truth".**
  It is exactly what the guard FSM defines — permitted / requires_approval /
  requires_auth / requires_entity, per tool. A claim about entitlements ("you get
  45 vacation days") references no policy variable here and is reported
  `out_of_scope`, in none of the five buckets. Value-claims wait on a typed
  policy-variable model, which is a separate spec.
- The CDV loop takes an injected executor rather than reaching into the harness
  directly, so it stays testable offline; wiring it to the real harness + scoring
  engine is a thin adapter, not a rewrite.

## Verification

M45–M46 add 49 tests (18 + 31); the full suite is 4325 passing. Four
`test_release_ladder` failures reproduce on unmodified `master` and are
clock-coupled, not caused by this work: the file hardcodes
`NOW = datetime(2026, 7, 7)` and compares it against real-clock registry stamps,
so it fails with negative observation hours on any day after that date.
`tests/verification/test_cdv_cli.py` is coupled to `FORCE_COLOR` in the same
family — Rich highlights bare integers, so `assert "4 · CONVERGENCE" in out`
misses when colour is forced on. Neither was edited.

M40–M44 added 139 new tests. Full suite green apart from 4 pre-existing `test_dist_quickstart`
failures that reproduce identically on clean master (they subprocess
`python -m agenttic` without `PYTHONPATH` — a local-env artifact). No existing
test was edited, and neither the scoring engine nor the Step 14 promotion gate
changed behaviour.
