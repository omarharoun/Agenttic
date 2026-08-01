# Pre-registration — first public evaluation subject

**Written 2026-08-01, before any run.** Everything below is a commitment made in
advance. Nothing here may be edited once a run has happened; corrections go in a
dated section at the bottom, so the difference between what we expected and what
we found stays legible.

This is the runbook's Day 1 gate. Its purpose is not bookkeeping — it is the
honesty device. A published prediction we got wrong is worth more than a report
with no prior, because a report with no prior cannot be wrong in public.

---

## The subject, pinned

| | |
|---|---|
| project | **OpenHands** (`OpenHands/OpenHands`, formerly `All-Hands-AI/OpenHands`) |
| release | `v1.8.0`, published 2026-07-30T17:07:53Z |
| commit | `c7a765d900df294cbbf0f405ae26c9cbbd0fcc29` |
| licence | MIT (GitHub SPDX; the LICENSE file text has **not** been read — do that before publishing) |
| activity | `pushed_at` 2026-08-01T16:51:25Z, not archived |
| stars | 82,778 at time of writing |

All figures fetched from `api.github.com` on 2026-08-01. The repository redirects
from its old owner; the numeric id `771302083` is the stable handle.

**Not a competitor.** All Hands AI sells a coding agent. We sell agent
verification. Evaluating a rival's product reads as a hit piece and would poison
the neutrality this exercise exists to establish.

**Chosen because we expect it to do WELL.** This is deliberate and
counter-intuitive: a weak target makes the write-up a takedown and everyone
discounts the finding. OpenHands is the reference scaffold behind several strong
SWE-bench Verified entries. If it scores well, our praise is credible and the
coverage gaps we find are the interesting part. If it scores badly despite its
reputation, that is a genuinely significant finding — but it has to arrive as a
finding, not as a target.

## The job we are evaluating it for

> Given a real bug report from a Python repository, produce a patch that fixes
> the described failure without breaking what already passed.

One sentence, on purpose. If the evaluation drifts from that sentence, the
evaluation is wrong, not the sentence.

## The prediction — dated 2026-08-01, before any run

Recorded so it can be checked against the result, including where it is wrong.

1. **It will pass a majority of the offline SWE-bench instances.** Estimate
   4 of 6 on `patch_generated`, 3 of 6 on `targets_gold_files`. Low confidence:
   the vendored offline split is six hand-picked instances, not a random sample.
2. **`pass^8` will be materially below `pass@1`.** This is the prediction we care
   about. Agent scaffolds are non-deterministic and the flakiness gap is the
   thing single-shot leaderboards cannot show. If `pass^8` comes back EQUAL to
   `pass@1` we treat that as a bug in our harness before we treat it as a fact
   about OpenHands — that exact equality was a real defect here until
   2026-08-01 (`632a8df`) and its absence is not proof of its absence.
3. **Coverage closure will be low — under 30%.** Six instances cannot exercise
   the situation space, and saying so with a number is the contribution.
4. **The most interesting output will be what was never tested, not the score.**
   If the report's headline is the pass rate, we will have written the wrong
   report.

## What this evaluation can and cannot claim

**Can.** Deterministic, code-scored criteria on the `coding` archetype, over a
suite that ships with the product (`swebench-verified-v1`). The rubric carries
**zero judge criteria**, so the PROVISIONAL judge cap — every judged criterion
in this build is provisional, and the two calibration gates are hardcoded shut —
cannot touch the headline number. That is why this subject was chosen first.

**Cannot, and the report must say so in these words:**

- **Nothing about multi-turn state.** `schema/archetype.py` registers
  `multi_turn_state` in `UNEXERCISABLE_FEATURES`: the harness delivers one dict
  as one user message, so there is no second turn to hold state across.
- **Nothing about the harness enforcing anything.** The honeypot battery is off
  by default and is not being run here, so no claim is made about whether
  OpenHands' own scaffold would stop a disallowed action.
- **Nothing about judged quality.** No judge criterion is scored, so nothing here
  speaks to whether the patch is *good* — only whether it exists and touches the
  files the gold patch touched.
- **Six instances is not SWE-bench.** Any number here describes six problems.
  The full 500-instance split is a separate, larger commitment.

## How it will be driven — and why that matters

Headless mode, its own published interface: `openhands --headless --json -t
"<task>"`, which streams JSONL where each line is an agent event. We do not patch
it. The events map to spans, which makes the trace **glass-box**.

That is not a convenience. `scoring/engine.py` currently drops only three checks
for a black-box target while seven registered checks read tool spans, four of
them with no text fallback — on a black-box trace a correct answer can score 0.0
and a fabricated one 1.0. A glass-box trace does not take that path at all. The
defect remains open and is recorded here because a reader deserves to know which
hazards this design avoided rather than fixed.

## Method commitments, made in advance

- **k = 8.** Chosen before seeing any result.
- **No retries.** An agent mistake is data; a retried failure is a contaminated
  measurement. If the harness itself breaks mid-run we re-run the WHOLE thing
  from a clean state and say so in the report.
- **Assertions on every trace, including the passing ones.**
- **This first pass is a DRY RUN and will not be published.** The decision to
  publish comes after seeing what the report actually says — and "we ran it and
  chose not to publish" is a legitimate outcome that will itself be recorded.

## Owed before anything is published

- [ ] Read the LICENSE file text rather than trusting the SPDX tag.
- [ ] Verify the 68.4% SWE-bench Verified figure attributed to OpenHands against
      the official leaderboard. It currently comes from aggregator pages and is a
      directional prior, **not** a citation.
- [ ] Notify the maintainers with the draft, the suite and the reproduction
      steps, and give them 3–5 working days before publishing.
