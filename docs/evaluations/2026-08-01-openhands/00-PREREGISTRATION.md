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

---

## CORRECTION — 2026-08-02, still before any run

**The subject named above conflates two different artifacts.** Found while
grounding the adapter in the subject's own source rather than trusting the
shortlist that produced this entry.

`OpenHands/OpenHands` (repo id `771302083`, 82,778 stars) at commit
`c7a765d900df294cbbf0f405ae26c9cbbd0fcc29` is **Agent Canvas** — in its own
README, *"the self-hosted developer control center for coding agents and
automations… run OpenHands, Claude Code, Codex, Gemini, or any ACP-compatible
agent."* It is a TypeScript/React/Electron application. It is not a Python coding
agent, it is not driven by `openhands --headless --json`, and it would map to a
different archetype entirely, if any.

The CLI with that interface is a **different repository**:

| | |
|---|---|
| repo | `OpenHands/OpenHands-CLI` |
| licence | MIT |
| stars | **232** |
| latest release | `1.16.0`, published 2026-05-08 (about three months ago) |
| tag commit | `2963442dacc7cea44e39b7c4e73724295c853465` |
| head commit | `2df8a2835d3f1bd2f2eadf5a7a2e1ad0dfb0d271`, dated 2026-06-27 |
| interface | its README documents `openhands --headless --json -t "task"` — VERIFIED |

A third repo, `OpenHands/software-agent-sdk` (MIT, 947 stars, pushed
2026-08-01), is *"a clean, modular SDK for building AI agents with OpenHands
V1"* — the most actively maintained of the three, but an SDK rather than a
runnable CLI.

### What this invalidates

1. **The star count above must never describe what we evaluate.** 82,778 belongs
   to the control centre. The CLI's own repo has 232. Publishing "we evaluated
   OpenHands, 82k stars" while driving a 232-star CLI would be exactly the
   misrepresentation this document exists to prevent — and it is the kind of
   thing a hostile reader finds in ten seconds.
2. **"Well enough known that the result is interesting" is now an open
   question.** The CLI is the published command-line interface of a widely used
   project, so its reach is larger than its star count suggests — but 232 is not
   82,778 and the report may not imply otherwise.
3. **The "expect it to do well" prior is unverified for this artifact.** It rested
   on OpenHands being the reference scaffold behind strong SWE-bench Verified
   entries. That reputation attaches to the Python agent lineage, now
   `software-agent-sdk`, not to the control centre and not necessarily to the
   packaged CLI at `1.16.0`.
4. **"Actively maintained" is weaker than claimed.** The flagship was pushed
   today; the CLI's last tagged release is three months old and its head commit
   is from 2026-06-27.

### What survives

The **interface** claim, which is the part the adapter depends on:
`openhands --headless --json -t "<task>"` is real and documented in the CLI's own
README. The glass-box argument — JSONL events map to spans, so the black-box
criterion defect in `scoring/engine.py` never applies — is unaffected.

The **suite** claim is unaffected: `swebench-verified-v1` is vendored, carries
zero judge criteria, and its behaviour under the coding archetype was measured
directly.

### Status

Subject is **NOT settled**. Adapter work continues, because it targets the CLI's
headless JSONL either way and is the same code for any of these candidates. No
run happens, and nothing is published, until the subject line above is replaced
by one that survives a reader checking it.

The original entry is left exactly as written. It is wrong, it is dated, and the
correction is dated after it — which is the only way a pre-registration is worth
anything.

---

## CORRECTION 2 — 2026-08-02, still before any evaluation run

The adapter was built, then the subject was run **once**, on a trivial task
("create hello.txt"), to check the adapter against reality. That single run
falsified three things asserted above or assumed while building. None of it
changes the plan; all of it changes what the report may claim.

**This was not an evaluation run.** No suite, no scoring, no k. It is disclosed
here because the pre-registration is the place where the record is kept, and a
run that happened is a run that happened.

### 1. "each line is an agent event" — false

> *"streams JSONL where each line is an agent event"* (the section above)

`--json` does **not** silence the human interface. Event lines arrive
interleaved with Rich terminal chrome: a startup banner, a boxed conversation
summary, `Goodbye! 👋`. **27 of the run's 33 stdout lines were decoration.**

The adapter had been treating every unparseable line as a dropped event, so it
would have attached "27 events could not be read" to every single trace — a
false alarm on every run, and the exact noise a genuinely dropped event would
have hidden in. Fixed: a line that never opened as an object is chrome and is
ignored; a line that opened as an object and failed to parse is lost evidence
and is counted.

### 2. The agent does not answer with a message

It calls the `finish` tool, and the answer is in `action.message`. An adapter
reading only `MessageEvent` — which is what reading the SDK's event schema
leads you to build — finds **no answer at all** and records a completed task as
a non-result. Every case would have come back a harness failure, and the report
would have blamed the subject for a defect that was ours. This is the finding
that most nearly wrecked the evaluation, and nothing short of running the binary
would have surfaced it.

### 3. There is no `--model` flag, and the model is ignored by default

The CLI takes the model from `LLM_MODEL`, and **only** when
`--override-with-envs` is passed; its own help says *"By default, environment
variables are ignored."* Two silent failure modes: pass `--model` and argparse
rejects the command, so every case is a non-result that reads like the subject
failing; or set the env var without the flag and the CLI quietly runs whatever
`openhands login` last stored.

**This is a provenance requirement, not a convenience.** A run that cannot say
which model produced the evidence is not evidence. The adapter now sets both
together, and when no model is pinned it says so on the trace rather than
letting a reader assume.

### What this does to the schedule

Nothing is invalidated. The interface claim survives in the form that matters —
`openhands --headless --json -t "<task>"` is real, it was driven end to end, and
the events map to spans, so the trace is glass-box exactly as argued. The subject
is still **NOT settled** (see Correction 1), and the dry run still does not
publish.

**Standing method note, added to the commitments above:** an adapter is not
finished when its offline tests pass. Reading the subject's source gives you the
schema of what it emits; only running the subject tells you what actually comes
out. Every future adapter gets one real smoke run against the live binary before
any evaluation is scheduled on it.
