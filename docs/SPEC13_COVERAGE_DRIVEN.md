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
| "86% passed" | Sign-off: closure + assertions + properties + bug curve (M44) |

## Milestones

| Milestone | Step | State | Tests |
| --- | --- | --- | --- |
| M40 — Assertions | 62 | ✅ | 49 |
| M41 — Coverage model | 59 | ✅ | 18 |
| M42 — Stimulus + CDV loop | 60–61 | ✅ | 40 |
| M43 — Formal (authorization layer) | 63 | ✅ | 18 |
| M44 — Sign-off + vPlan | 64 | ✅ | 14 |

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
- The CDV loop takes an injected executor rather than reaching into the harness
  directly, so it stays testable offline; wiring it to the real harness + scoring
  engine is a thin adapter, not a rewrite.

## Verification

139 new tests. Full suite green apart from 4 pre-existing `test_dist_quickstart`
failures that reproduce identically on clean master (they subprocess
`python -m agenttic` without `PYTHONPATH` — a local-env artifact). No existing
test was edited, and neither the scoring engine nor the Step 14 promotion gate
changed behaviour.
