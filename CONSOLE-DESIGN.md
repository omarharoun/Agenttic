# Agenttic Console — design

**Status:** design only, no implementation. **Date:** 2026-08-10.
**Boundary:** clean-room. Everything about iii below comes from its *rendered
product* — the public docs at `iii.dev/docs` and the marketing site. No file
from `github.com/iii-hq/iii` (not `console/`, not `engine/`) was opened, cloned,
or fetched. Sources are listed per-observation in §1 and collected in §7.

> ⚠️ **Untracked-file hazard — commit before anything else.** This file and the
> F1–F4/M50 work it depends on are **untracked or uncommitted** (`scope.py`,
> `suite_validation.py`, the modified `scorecard.py`/`engine.py`/
> `scorecard_report.py`/`issues.py`/`executions.py`, their tests, and the two
> working memos). A concurrent session has wiped an untracked file in this
> checkout before. **Commit the F1–F4/M50 set and this doc now.** The console
> renders exactly those new fields; losing them loses the design's data.

---

## 0. One correction to the brief

The brief says console work sits on *unpushed commits*. It does not.
`git rev-list --left-right --count origin/master...master` is **0 / 0** — master
is fully synced with origin. The run-timeline, playground grouping, and
fault-fate graph are **already committed and pushed**:

- run-as-timeline → [`515360c`] `ui/src/components/TraceTimeline.tsx`
- playground nav grouping → [`2fb071f`] `ui/src/AppShell.tsx`
- scenario-runs-by-fault-fate → [`51abb2f`] `ui/src/components/FaultFateBar.tsx`
- findings rail → [`18769a5`] `ui/src/pages/ScenariosPage.tsx`

What *is* uncommitted is the **F1–F4/M50 backend/schema work** (see the hazard
box). That is the thing at risk, and it is the thing this design consumes.

---

## 1. What iii's console actually is (observed, with sources)

iii is a **backend-orchestration runtime** (workers/triggers/functions) whose
console is an *operational cockpit* over a *running* system. Its whole
observability thesis is "observe = open the trace," OTel-native, every span in
one view. [S1][S2]

The console is a sidebar app with one page per runtime noun. Observed IA and
detail views, from the Console doc [S3] and the "Observe everything" tutorial
[S4]:

| Page | What it shows | Notable interaction |
|---|---|---|
| **Workers** | connected worker processes; name/ID, SDK version, isolation badge, PID, function count, **in-flight invocations**, uptime | detail = live metrics (memory, CPU, event-loop lag) + registered functions |
| **Functions** | registered functions **grouped by namespace prefix**; toggle system fns | detail = schema-derived description + **editable request body + Invoke button + result with call duration** |
| **Triggers** | filter tabs All/HTTP/Cron/Event/Other; type badge, bound fn | **type-specific test harness** — HTTP body editor, Cron "Run Now", Event "Emit Event" |
| **States** | three-pane KV browser (groups / items table / detail); search + pagination | Add/Edit/Delete; **explicitly *not* live — manual refresh** |
| **Streams** | WebSocket monitor; in/out counters, throughput, latency | **live**; message rows → full payload; export JSON |
| **Queues** | topics, broker, **DLQ badge**, subscriber count | Overview (live stats + test publisher) + Dead-Letters (retry/delete); keyboard j/k, Enter, 1/2 |
| **Traces** | OTel viz in **four modes: Waterfall / Flame Graph / Trace Map / Flow (execution DAG)**; filters trace-id/service/span/status/duration/time/attrs | **invocations stream in live**; click a row → **waterfall of timed spans, sortable slowest-first**; span detail = metadata, tags, logs, errors, baggage |
| **Logs** | structured OTel viewer; timestamp, severity badge, trace-id, source, message | filters severity/time/full-text; **trace-id links to the trace** |
| **Config** | resolved runtime values | read-only confirmation |

The load-bearing patterns, distilled:

1. **The trace waterfall is the primary drill-in.** A run is a row; a click
   opens a timed span tree; spans sort slowest-first. [S4]
2. **Live-streaming list.** Rows appear as work executes. [S4]
3. **Bidirectional log↔trace linking** by shared id. [S3]
4. **Inline "act on it"** everywhere — Invoke, Run Now, Emit, test-publish,
   retry-DLQ. The console *operates* the system, it doesn't just read it. [S3]
5. **Honest liveness labels** — the docs *tell you* States isn't live but
   Streams is, rather than pretending uniform freshness. [S3]
6. **Structured facet filters + grouping** (by namespace, by trigger type, by
   span status/duration). [S3]

---

## 2. What's worth borrowing, and why

Borrow *interaction patterns*, not screens. Each of these has a direct Agenttic
analogue and most are already half-built here.

- **Trace waterfall as the run drill-in.** This is the single best steal. It
  already exists as [`TraceTimeline.tsx`](ui/src/components/TraceTimeline.tsx).
  The upgrade iii implies: sort/scan by cost or duration, and make every span a
  target for deep-linking (§5, slice 3).
- **Live-streaming run list.** [`ExecutionsPage`](ui/src/pages/ExecutionsPage.tsx)
  already streams progress. iii confirms the shape: rows stream, click to drill.
  Keep it; don't rebuild it.
- **Log↔trace linking → finding↔span linking.** iii links a log line to its
  span by id. Agenttic's analogue is *evidence provenance*:
  `AssertionResult.span_index` already records **where** an assertion broke.
  Wire each finding/assertion/coverage-bin to the span in the timeline. Cheap,
  high value — it turns the findings rail from a list into evidence.
- **Facet filters + grouping.** iii groups functions by namespace and filters
  spans by status/duration. Agenttic: group criteria by tag/dimension, group
  coverpoints by dimension, filter cases by pass/fail/errored/N-A/criterion.
  [`IssuesPage`](ui/src/pages/IssuesPage.tsx) already ranks worst-first; extend
  that discipline.
- **Honest-liveness discipline → honest-confidence discipline.** iii's habit of
  the UI telling the truth about what's live is the same *muscle* Agenttic needs
  for telling the truth about what's *calibrated* and *in scope*. Different
  content, identical principle: the UI never over-claims the state of its data.

---

## 3. What does **not** transfer, and why

- **The operational cockpit (Invoke / Run Now / Emit / publish / retry-DLQ).**
  iii lets you poke a live system. Agenttic's core artifact — the scorecard and
  the signed passport — is **immutable evidence you must not be able to fudge**.
  The console is overwhelmingly *read*. The **only** place Agenttic legitimately
  "acts on the system" is the Playground/Training-Camp, which is deliberately
  fenced off from the certified path — keep that fence.
- **Runtime-infra nouns: Queues/DLQ/backpressure, Streams monitor, KV State
  browser.** No analogue in a verification artifact. Drop entirely — copying
  them would be cargo-culting iii's IA.
- **The Worker/Trigger/Function information architecture.** Agenttic's nouns are
  Agent · Suite(+Rubric) · Run(→trace) · Scorecard · Passport(+scope) ·
  Findings · Coverage · Assertions · Regression. Mirroring iii's nav would model
  the wrong domain.
- **"Steer a live deployment."** Agenttic runs are **batch jobs that finish and
  freeze.** Real-time matters *during* a run; after, **immutability and
  provenance matter more than freshness**.
- **A bare headline number.** iii shows duration freely — it's not a
  trustworthiness claim. Agenttic must **not** lead with a pass-rate; that's the
  product thesis and a hard rule. iii's metric-forward instinct is exactly what
  to resist here (§5).
- **Flame Graph / Trace Map.** Over-built for our depth. The Waterfall covers
  the run; the "Flow (execution DAG)" is already covered by
  [`ReplayCanvas`](ui/src/canvas/ReplayCanvas.tsx) node playback. Don't build the
  other two.

---

## 4. Proposed information architecture (grounded in the real shapes)

Four altitudes. Every noun maps to a schema object that actually exists (file
refs are the data source, not a UI file).

**A. Fleet / overview** — [`DashboardPage`](ui/src/pages/DashboardPage.tsx).
Agents × their latest scorecard, each row shown as **`verification_status`
(PASS / INCOMPLETE / FAIL) + a scope chip**, never a pass-rate ranking.
`verification_status` is computed in [scorecard.py:155](src/agenttic/schema/scorecard.py).
⚠️ [`LeaderboardPage`](ui/src/pages/LeaderboardPage.tsx) currently ranks by
pass-rate — that contradicts the thesis and must be reframed or demoted (§5.4).

**B. Scorecard — the artifact (the heart of the console).** Data:
[`Scorecard`](src/agenttic/schema/scorecard.py). Order, top to bottom:

1. **Verdict + fence header** — `verification_status` and, inline and
   equal-weight, the scope fence (§5.3). Pass-rate appears here only **demoted
   and labelled "unscoped"** (`task_success_rate` + Wilson interval
   `success_wilson_low/high`).
2. **Criterion ledger** — [`ScorecardCard`](ui/src/components/ds/Scorecard.tsx)
   over `per_criterion_means` + each `CriterionScore`. Each row shows scorer
   (`code|judge|fi`), the **calibration status string** from
   [`criterion_status()`](src/agenttic/reporting/scorecard_report.py) —
   `deterministic` / `judge · calibrated α=0.89 (ceiling 0.94)` /
   `judge · PROVISIONAL (uncalibrated)` — the **N/A count**
   (`per_criterion_na_counts`, F2a), and the score rendered by tone (§5.1).
3. **Coverage** — [`CoverageWheel`](ui/src/components/ds/CoverageWheel.tsx) over
   `coverage`; headline `trace_closure` vs `closure_target`; **unexercised bins
   and unmeasurable coverpoints named, not hidden** (`unhit`, `not_measurable_reason`).
4. **Assertions** — `assertions` list: pass / violation / **unexercised
   (vacuous, never a pass)** / error; counts `assertion_violations`,
   `assertions_unexercised`, `assertion_errors`.
5. **Findings** — classified `agent_finding | suite_finding | evidence_finding`
   ([scorecard_report.py:85](src/agenttic/reporting/scorecard_report.py)).
   `evidence_finding` = uncalibrated judge; render it as provisional, not as an
   agent failure.
6. **Regression vs baseline** — [`ABComparison`](src/agenttic/schema/ab.py):
   `success_delta`, McNemar, per-criterion deltas with CIs, `flipped_cases`.

**C. Run — a single trace.** Data: `RunScore` + spans. This is iii's trace-drill
analogue and is largely built:
[`ScenariosPage`](ui/src/pages/ScenariosPage.tsx) two-column detail +
[`TraceTimeline`](ui/src/components/TraceTimeline.tsx) waterfall +
`RunFindings` rail. Add finding↔span deep-links (§5, slice 3). A run has **no
verdict of its own** — pass/fail lives on the scorecard's assertions; the rail
must stay a re-statement of stored facts, not a judgment.

**D. Passport — public certificate.** Data:
[`BehavioralScope`](src/agenttic/passport/scope.py). The `/certified/:id` doc is
already redesigned (certdoc deploy). Add the **scope fence explicitly**:
`verified_capabilities` and `provisional_capabilities` as **two lists that
cannot merge**, plus `coverage_holes`, `not_measured`, and unexercised
assertions.

---

## 5. Provisional/verified & scope-as-fence — visual enforcement

This is the part the product lives or dies on. F1 just fixed a report that let a
reader squint "calibrated" off an uncalibrated criterion; the console must not
reintroduce it. Enforcement is **structural (in shared components and data),
not per-page discipline.**

### 5.1 Provisional is a different *type*, not a dimmer shade

- A provisional score must **never touch the pass→fail colour ramp**. It gets its
  own token (`--score-provisional`) that reads as *withheld/unknown*, not
  *slightly-passed*. No green. (Tokens only — no raw hex in components; hard
  rule.)
- [`ScoreValue`](ui/src/components/ds/Scorecard.tsx) already has a
  `tone: "provisional"`. Enforce: **if `CriterionScore.calibrated === false`,
  tone is `provisional`, full stop**, and the number renders annotated
  ("PROVISIONAL — not calibrated"), never as a naked `0.87` that scans as a
  grade. A bare provisional number is the F1 bug in pixels.

### 5.2 One source of truth, derived not decided

- The UI must read calibration state from the **same derivation the report and
  passport use** — the `criterion_status()` string, itself derived from the
  stored `CalibrationRecord` (fail-closed: no record → PROVISIONAL). F4 closed
  the fail-open `get("calibrated", True)` defaults in
  [`issues.py`](src/agenttic/issues.py) and
  [`executions.py`](src/agenttic/server/routes/executions.py); the console must
  mirror that — **consume the derived status, never re-decide `calibrated` from a
  payload bool.**
- `ProvenanceBadge`, `ScoreValue`, `ScorecardCard` have **exactly one
  implementation each** (hard rule). One component = one place the rule is
  enforced = it cannot be misread on one page and right on another.

### 5.3 The scope statement is a fence, not a badge

- **The fence travels with every status, physically adjacent and equal weight —
  never a separate tab you can skip.** Any place a verdict/capability shows, it
  renders inline: `coverage_holes` count, `not_measured` count,
  `assertions_unexercised` count, `provisional_capabilities` count,
  `per_criterion_na_counts`. Non-zero → the verdict is visibly qualified:
  *"PASS within scope — 3 bins unexercised · 2 dimensions not measured · 1
  criterion provisional."*
- **You cannot render the verdict colour without the fence.** Build them as one
  component (`VerdictWithScope`) so there is no code path that emits the green
  alone.

### 5.4 Never lead with pass-rate

The headline is `verification_status` + scope. `task_success_rate` is demoted and
labelled **unscoped** (matches the F5 report pins and the score-vs-pass-rate
labelling already shipped). What leads instead: coverage closure + calibration
completeness.

### 5.5 Passport: two lists that cannot merge

`BehavioralScope.require_complete()` already raises if any
`verified_capabilities` entry is PROVISIONAL, and provisional criteria are routed
to `provisional_capabilities` at the data layer. The UI mirrors this by rendering
from the **two distinct arrays** — never a single merged-and-filtered list —
so a provisional capability *physically cannot* appear under "Verified."

---

## 6. What exists vs what's new

**Live on master (reuse, don't rebuild):**
- Design tokens, both themes, colour-blind-validated palette —
  [`tokens.css`](ui/src/design/tokens.css)
- Shared `ds/` components: ProvenanceBadge, ScoreValue, ScorecardCard,
  CoverageWheel, RefusalNotice, primitives — [`ds/`](ui/src/components/ds/)
- Run detail: two-column layout, `RunFindings` rail, `TraceTimeline`,
  `FaultFateBar` — [`ScenariosPage`](ui/src/pages/ScenariosPage.tsx)
- Run list + live progress — [`ExecutionsPage`](ui/src/pages/ExecutionsPage.tsx);
  scorecard render from API — [`ResultsPanel`](ui/src/panels/ResultsPanel.tsx)
- Public cert doc — `/certified/:id` (certdoc deploy)
- Full page set (Dashboard, build, Results history, Capabilities, Issues,
  Compare, Certifications, Hardening, Agents, Billing, Settings, Playground group)

**Stale — tokens landed, content did not:**
- `ScorecardCard` renders **demo/sample data** on the landing; the console's real
  scorecard render (`ResultsPanel`) ships **whatever the backend returns** —
  **F1–F4/M50 fields are not styled** (no calibration-status rendering, no N/A
  column, no CI, no distinct provisional type).
- `ExecutionsPage` detail, `ResultsHistoryPage`, `ComparePage` detail — never
  rebuilt to the mockup; render scorecard as-is.
- `LeaderboardPage` — pass-rate ranking; contradicts the thesis.

**New (must build):**
- The **Scorecard artifact view** (§4-B) leading with verdict+fence.
- **`VerdictWithScope`** component (§5.3) — verdict colour and fence, one unit.
- **Provisional-as-type** guard in `ScoreValue`/`ProvenanceBadge` (§5.1–5.2).
- **`BehavioralScope` fence UI** for scorecard + passport (§5.5) — `scope.py` is
  imported by nothing today.
- **Finding/assertion/bin ↔ span deep-links** (`span_index` → `TraceTimeline`).
- **Regression block** (`ABComparison`) surfaced in Compare / scorecard.

**Backend-blocked (flag, don't fake):** assertions are stored on the
`Scorecard`, not on a scenario run — so the run detail cannot show an assertions
rail until the backend attaches assertion results to the run. Leave it honestly
absent rather than inventing per-run assertions.

---

## 7. Build order — cheapest first

**Slice 0 — commit the F1–F4/M50 set + this doc.** Prerequisite, not console
work, but the console renders these fields and they're untracked. Do it first.

**Slice 1 (cheapest, highest thesis payoff) — the Scorecard artifact view.**
Rebuild the scorecard render to lead with `verification_status` + scope fence and
show the **real F1 calibration status** per criterion, with provisional as a
distinct type (§5.1–5.4). Almost entirely **wiring + one token +
one guard** over components that already exist (ScorecardCard, ScoreValue,
ProvenanceBadge, CoverageWheel). This is the slice that closes the "console lets
you misread calibrated" gap — the whole reason for the exercise.

**Slice 2 — `VerdictWithScope` + `BehavioralScope` fence.** Build the one
component that can't emit a verdict without its fence; feed it
`scope.from_scorecard()`. Reuse it on scorecard and passport. Turns `scope.py`
from orphan into UI.

**Slice 3 — finding/assertion/bin ↔ span deep-links.** `AssertionResult.span_index`
→ scroll/highlight in `TraceTimeline`. Small, reuses TraceTimeline + RunFindings,
delivers iii's best borrowed pattern (evidence you can click into).

**Slice 4 — regression block.** Surface `ABComparison` (delta, McNemar,
flipped cases) in [`ComparePage`](ui/src/pages/ComparePage.tsx) / scorecard
"vs baseline." Schema exists; this is presentation.

**Slice 5 — reframe fleet/leaderboard.** Dashboard + Leaderboard lead with
`verification_status` + scope; pass-rate demoted and labelled unscoped.

**Deferred (backend) — per-run assertions rail** (see §6 backend-blocked).

Start with Slice 1: it's the smallest diff, reuses the most, and it's the one
that makes the console honest about calibration — which is the product.

---

## 8. Sources (clean-room — rendered product only)

- [S1] iii marketing/home — `https://iii.dev/` (observability as a system trait;
  "observe = open the trace")
- [S2] iii repo landing description (public README summary via search, **not the
  source tree**) — `https://github.com/iii-hq/iii` (workers/triggers/functions;
  "compose, extend, observe in real-time")
- [S3] **Console doc** — `https://iii.dev/docs/using-iii/console.md` (full page
  IA: Workers/Functions/Triggers/States/Streams/Queues/Traces/Logs/Config, detail
  panels, liveness, keyboard nav)
- [S4] **"Observe everything" tutorial** —
  `https://iii.dev/docs/tutorials/linkly/observability.md` (live-streaming
  traces, click-to-waterfall, spans sorted slowest-first, screenshot described)
- [S5] Doc manifest — `https://iii.dev/docs/llms.txt` (page inventory)
- [S6] Observability concepts — `https://iii.dev/docs/creating-workers/observability.md`
  (OTel traces/metrics/logs)

No file under `iii-hq/iii/console/` or `iii-hq/iii/engine/` was read. All UI
observations derive from the rendered docs and marketing above.
