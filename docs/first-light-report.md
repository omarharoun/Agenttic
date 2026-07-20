# First Light Report — SPEC-8 Step 34

*2026-07-20 · pilot `pilot-refund-support` vs the reference agent (claude-sonnet-4-6),
judged by the configured judge stack, on production (agenttic.io droplet, Postgres
registry). Every number below is from real model calls with real dollar costs.*

## The headline artifacts

| Artifact | Value |
|---|---|
| Real scorecard | `b50bb9c29d4f` — **69.4% success** (n=36 scored, Wilson 95%: 53–82%) |
| Cost of that run | **$1.48** ($0.37 agent execution + $1.11 judging) · $0.0101/case exec |
| p95 latency | 14.1 s/case |
| Learn round (held-out) | **Honest null**: 3 candidates generated, all rejected at the cheap screen ("worse than baseline"); **no candidate promoted this run** |
| Reliability (pass^k) | **Not yet measured** — the k=8 run (296 trials) was halted mid-flight by **API credit exhaustion**; single-trial numbers above are labelled as such (Hard Rule 34). Re-run pending account top-up (~$10–12). |
| Total real spend this first light | ≈ **$14.50** (generate + 2 scored runs + learn round + the partial k=8) |

## What broke (the spec said something would)

Four platform bugs, all found by ~$14 of real usage, all fixed + regression-tested same-day:

1. **`agenttic run` had no k/trials flag** — the harness supported `trials_per_case`
   since M22 but the CLI never exposed it. Fixed (`--trials/-k`, budget projection
   multiplied and shown).
2. **Continuous checks crashed scoring** — `answer_accuracy` returned token-F1
   0.041; `CriterionScore` enforces {0, 0.5, 1} (Hard Rule 3) → ValidationError →
   15/37 cases recorded as scoring errors. Engine now snaps to the criterion scale.
3. **Gate/engine asymmetry** — a case whose check could not run (missing
   `expected.goal_state`) *passed* the oracle gate but errored on every real
   scoring run. The gate now fails such cases outright.
4. **zip truncation in scoring** — with k trials the harness returns k×n traces;
   `zip(traces, cases)` silently scored only the first n. 259 of 296 paid-for
   trial traces were never scored. Now paired by `test_case_id`.

Operational gaps noted (not yet fixed): review files written inside the ephemeral
container are lost (durable artifacts live in the registry, so nothing was
unrecoverable); a terminal upstream error (credit exhaustion) does not
circuit-break the remaining cases — 296 runs were attempted against a dead API.

## The suite integrity gates earned their keep

The real `generate` round (Opus, from a realistic e-commerce support job
description) produced **98 draft cases. The three integrity gates blocked 61 of
them** before any money was spent running agents:

- 54 UNSOLVABLE-AS-WRITTEN — six invented aliases for one KB tool, tools the
  agent doesn't have, oracles missing tool sequences, rubric checks reading
  expected-fields the cases never carried, stateful checks with no environment
- 12 vacuous — a do-nothing agent passed them
- 13 exploitable — a no-LLM heuristic cheater passed them (leaked answers)

Four mechanical remediation rounds produced **v5: 37 cases, all three gates
green** — the first suite approved through the Hard-Rule-27 gate on real data.

## Real agent findings (not tooling)

- The reference agent asks clarifying questions instead of computing on several
  arithmetic cases (`calculate_shipping_cost`, `apply_multi_item_discount`) —
  legitimate task failures, prime learn-round material.
- `refuse_unknown_policy_and_escalate` failures are real behavior gaps.
- Judge behavior: judging costs ~3× agent execution on this suite; judge parse
  behavior was clean once scores were snapped to scale.

## Claims status (Hard Rule 36)

- "We raised X% with zero regressions" — **retired from all material.** This
  first light's learn round produced an honest null; the claim returns only
  with lineage attached.
- "Suites ship provably solvable, non-vacuous, cheat-resistant" — **TRUE with
  artifacts**: integrity report for `pilot-refund-support` v5, stored in the
  production registry, all gates green.

## Next

1. Top up API credits → re-run `agenttic run --agent reference-support --suite
   pilot-refund-support --trials 8` → record the pass^k curve + flakiness gap
   (the missing acceptance item).
2. Step 35: human labels in the calibration workspace (30–50/criterion, ≥20
   double-labelled for the human–human ceiling).
