# Token reconciliation (SPEC-11 Step 50)

Two artifacts expressed the "Chronometer" language independently: the console
(`ui/`, SPEC-4) and the marketing landing reference (`agenttic-landing.html`).
This is the audit trail of merging them into one source of truth,
`ui/src/design/tokens.css`. The landing HTML was a **visual reference, not
source to clone** — its *system* was extracted and reconciled with the console's
existing, battle-tested tokens; the console was not rebuilt.

Default resolution stance: where the two disagreed, the **console token wins**
(it already ships in both dark and light with soft/border companions across
~3,400 lines), and the landing route conforms — unless the landing's value is
the better product decision, in which case both sides move. Every conflict:

| Group | Console value | Landing reference | Resolved to | Why |
| --- | --- | --- | --- |
| **Default theme** | dark (`--bg #08090B`) | light (`--paper #F6F3EC`) | keep **both**; console defaults dark, landing defaults light | The spec: "the landing inherits [the console's] themes, defaulting to light." Not a token conflict — a per-surface default. |
| **Light background** | opaline `#ECEDEA` (cool) | warm paper `#F6F3EC` | **`#ECEDEA`** (console) | One light background for the whole product; the console's opaline is already tuned against every component. The landing gives up its slightly warmer paper for one system. |
| **Accent / gilt (dark)** | `--clay #C9A227` metallic gold | `--gilt #9A7B3F` muted bronze | **`#C9A227`** | The console's gilt ramp has 9 tuned steps used everywhere; the landing's single muted gilt reconciles to it. |
| **Accent / gilt (light)** | `#8A6D14` deep gold | `#9A7B3F` | **`#8A6D14`** | Same ramp, light-tuned; keeps the accent identical to the app. |
| **Score: pass** | `--ok` (`#6FA07A` dark / `#45704F` light) | `--pass #3E6E4B` | **`--score-pass: var(--ok)`** | Near-identical; alias onto the existing per-theme status ramp so there is one hue per meaning per theme. |
| **Score: provisional** | `--wait` (`#C9A34E` / `#93701F`) | `--prov #8A6D2F` | **`--score-provisional: var(--wait)`** | Amber "not-yet-calibrated" hue already exists in both themes. |
| **Score: deterministic** | `--info` (`#7D96B8` / `#3C5B82`) | `--steel #4A5A6A` | **`--score-deterministic: var(--info)`** | The reference's "steel" is the console's info-blue; unify. |
| **Score: fail** | `--fail` (`#C8503F` / `#A63E2E`) | `--fail #8C3A2E` | **`--score-fail: var(--fail)`** | Same meaning, one hue per theme. |
| **Fonts** | self-hosted Marcellus / Geist / Geist Mono | Google Fonts `<link>` | **self-hosted** | The console vendored the subsets to kill render-blocking; the landing's Google-Fonts link is dropped. Families are identical, so no visual change. |
| **Type display size** | fixed `--t-display 56px` | `clamp(38px,6.5vw,74px)` | keep **`--t-display`** token; the landing hero may apply a fluid `clamp()` on top | The scale tokens stay fixed and shared; fluid hero sizing is a landing-route concern, not a global token. |
| **Radii** | 2px family (`--r-xs 2px … --r-2xl 14px`) | 2–6px ad hoc | **console 2px family** | Machined-edge system already defined; the landing's radii map onto it. |
| **Motion curve** | `--ease-escape cubic-bezier(0.3,0,0.1,1)` | `cubic-bezier(.4,.1,.3,1)` | **`--ease-escape`** | One named escapement curve; the reference's near-variant reconciles to it. |
| **Spacing** | 4px base (`--sp-1 … --sp-20`) | 4px-ish ad hoc | **console 4px scale** | Already identical in spirit; console scale is canonical. |

## Semantic score tokens — the shared product vocabulary

`--score-pass`, `--score-provisional`, `--score-deterministic`, `--score-fail`
(each with `-soft` / `-border` companions) are **defined once** in
`design/tokens.css` and used identically by the console scorecard and the
landing's demo scorecard (Hard Rule 48). They alias the dial-tuned status ramp
(`--ok` / `--wait` / `--info` / `--fail`) so each meaning resolves to exactly one
hue per theme, and the aliases resolve per-theme at use-site (a `--score-pass`
read under `html[data-theme=light]` yields the light `--ok`).

## Enforcement

`npm run lint:tokens` (`ui/scripts/check-tokens.mjs`) fails the build on any raw
hex colour in `src/pages`, `src/components`, or the landing route — SPEC-4 Hard
Rule 20 extended to the landing (Hard Rule 47). Colours there must be a
`var(--token)` or a `tokens.ts` reference.

## Visual regression baselines

`ui/e2e/visual.spec.ts` holds the token layer to its appearance: 16 committed
screenshots (5 console screens + 3 public routes, each in both themes) plus a
mechanism check that a token override visibly changes **both** a console screen
and the landing — because if it did not, the snapshots would pass whether or not
those surfaces actually draw from `tokens.css`.

Any future edit to `design/tokens.css` that moves a pixel on these screens fails
the suite. The diff must then be either fixed or accepted and explained here.
That is what makes this file a record rather than a one-time note.

**Baselines are captured against stubbed API responses, not a live backend.** A
snapshot is only evidence if identical input yields identical pixels; pointing
these at a database would make them a test of whatever it happened to contain.
The fixtures live in `e2e/support/console.ts` and match the real endpoint
shapes — the list routes return bare arrays (`server/routes/resources.py`
`return rows`), and stubbing them as `{items: […]}` crashes DashboardPage, which
is how that mismatch was found.

### What the suite can actually detect

Sensitivity was measured, not assumed. Repainting `--accent` in the light theme
and re-running:

| Tolerance | Screens that caught it |
| --- | --- |
| `maxDiffPixelRatio: 0.002` | 2 of 8 |
| `maxDiffPixels: 120` | 9 |

A *ratio* scales the blind spot with the page: on the ~1280×5000 landing page
0.002 permits ~12,800 changed pixels, so a whole button can change colour and
still pass. The absolute budget does not grow with page size. It was set against
three consecutive runs that diffed at zero, so the headroom absorbs antialiasing.

**It does not, however, absorb only antialiasing — measured, not assumed.**
Replacing the run fixture's coverage blob (below) moved the console's headline
closure figure from `91%` to `47%` and its unexercised-property count from `2` to
`5`, on two screens each. Counted with PIL against the previous baselines:

| Screen | pixels changed | of those, delta > 32/255 | gate verdict |
| --- | --- | --- | --- |
| `results-dark` | 180 | 123 | **passed** |
| `results-light` | 179 | 125 | **passed** |
| `dashboard-dark` | 180 | 123 | **passed** |
| `dashboard-light` | 179 | 129 | failed |

Three of the four sat under `maxDiffPixels: 120` and reported green while the
number on the page had changed by 44 points. A two-digit percentage in 13px UI
type is simply smaller than the budget. On a product whose claim is an honest
account of coverage, the figure is the one thing the gate must not be able to
miss — and `--update-snapshots` (default `changed`) will not even rewrite a
baseline it scored as passing, so a stale reference survives a regeneration pass.
`--update-snapshots=all` was needed to move those three.

This is a finding against `playwright.config.ts`, not against these baselines,
and it is recorded rather than fixed because that file belongs to another
workstream. A budget tight enough for a changed digit (single figures, with the
antialiasing sources listed below already removed) is the fix; loosening the
budget to make a flaky screen pass would re-open it.

Three sources of nondeterminism had to be removed before the baselines meant
anything, each of which had produced a green-but-empty check:

- **Theme.** Setting `data-theme` after load is overwritten by the no-flash
  script in `index.html` and by the app's own theme effect — `settings-dark.png`
  and `settings-light.png` came out byte-identical. The preference is now set in
  `localStorage` before boot, and `settle()` *asserts* the theme took effect
  rather than setting it, so a failure is loud instead of silent.
- **Clock.** Relative timestamps drift against fixed fixtures; `page.clock`
  is pinned to the same instant the fixtures use.
- **Data race.** Waiting on a container resolves before the API promises settle,
  so a screenshot could catch either the loading or the loaded render. Captures
  now wait for network idle.

### Accepted baseline changes

Four baselines were regenerated deliberately. Recorded here because the
criterion is "unchanged **or their diffs are explained**", and an updated
snapshot with no explanation is indistinguishable from an accident.

**landing (dark + light)** — the page was rewritten. The hero headline is now
`A certificate is not a participation award.`, taken verbatim from the
`SignoffRefused` message in `certification/attest.py`; a three-figure band and
the tool's actual refusal transcript were added; the side-by-side
`ComparisonTable` was cut (three of its seven rows made categorical claims about
competitors' products, which a single counterexample breaks). The h1 measure
went 15ch → 18ch with `text-wrap: balance`, because at 15ch the new headline
broke as "A certificate is / not a / participation / award" — a two-word orphan
in 66px display serif.

**pricing (dark + light)** — not edited directly. `.lp-hero h1` is shared by
both public routes, so the measure/`text-wrap` change reached the pricing hero
too. Checked rather than accepted: it now sets as "Priced by what / has to be
verified." on two balanced lines. This is the coupling a snapshot suite exists to
surface — a change made for one page landing on another.

**capabilities (dark + light)** — the previous baselines were **photographs of a
crashed page**, 1280×737 of React Router's error boundary. The stub served
`{dimensions, checks, models}` while `CapabilitiesPage` reads
`coverage.baseline.*` and `supply_chain.mcp_server.checks`. The new baselines are
the real 1280×3546 page.

That failure is the reason `loaded()` now refuses to snapshot an error boundary.
A crash screen is a *stable* image — it captures cleanly and diffs clean forever
while asserting nothing — so it survives exactly the checks a snapshot suite
performs. It surfaced only because stack traces embed hashed asset filenames, so
a rebuild shifted 1,736 pixels of text inside the error message.

Two further defects were hidden underneath it, neither reachable while the page
crashed:

- `supply_chain` was stubbed as `[]`, so `sc.mcp_server` was `undefined`. The
  fixture is now the **real response**, captured by calling
  `server/routes/capabilities.py` directly rather than transcribed from the
  page's field accesses — two hand-written attempts were wrong in two different
  places.
- The provisional dimensions rendered as `intentemotional_registerpolicy_vector`
  — three names mapped to `<code>` with no separator. On a page whose subtitle is
  "enumerated from the live registries — not a brochure", printing an invented-
  looking identifier is the precise opposite of the claim.

### Accepted baseline changes — the run's coverage blob (P0)

The stub in `e2e/support/console.ts` declared a coverage **headline** and nothing
underneath it: no `per_coverpoint` key at all. Two things followed, and both were
real.

`NeverExercised` — "what this run never exercised", the table this product exists
to print — returns `null` when that key is absent, so it rendered **nothing** on
every console snapshot in both themes. The gate photographed a blank space and
diffed it clean forever. That is how the cell computing
`Math.round((v.closure ?? 0) * 100)` shipped: it printed `0%` for a coverpoint
whose closure is `null` *because nothing measures it*, and no camera was pointed
at the row. A fixture showing only the happy path is not a weaker test; it is the
mechanism by which the defect stayed invisible.

And the headline itself was a literal. `trace_closure: 0.91` sat beside an
assertion roll-up of `{total: 8, violations: 0, unexercised: 2}` with nothing
making the four numbers agree with each other or with any possible run.

The blob is now **captured from the shipped collector**, the same rule
`capabilities.fixture.json` is under and for the same reason — two earlier
hand-written attempts at that file were wrong in two different places, and each
wrong guess rendered a crash the snapshot happily photographed. `collect()` with
`coverage/models/conversational_transactional.seed_model()` was run over a
14-case support-triage suite whose deterministic bins are earned from real spans
(declared `http.response.status_code` 503/429, an `error.type` timeout, a tool
result reading "account not found", a real `escalate_to_agent` call, a
`max_steps` attribute). It lives in `e2e/support/coverage.fixture.json`; the
headline fell out at **46.5%**, which is the mean over measurable coverpoints and
crosses, not a number anyone chose.

It carries all three render states on purpose:

| state | coverpoint | must render |
| --- | --- | --- |
| measured, with gaps | `trajectory` 0.8889, `budget_exceeded` unhit | `89%` |
| measured, exhibited nothing | `action_risk` 0.0, four bins unhit | `0%` |
| **not measurable** | `session_shape`, closure `null`, `unhit` `[]` | `not measurable` |

`action_risk` at zero is not a rounding artefact and must not be softened: every
tool in the suite is an opaque `mcp__acme__run`, which post-hardening earns no
risk bin, so the run touched tools and could not place a single one. That is a
real finding. The correction was never "stop printing 0%" — it was "stop printing
0% for something you did not measure", and a fixture that cannot tell those two
apart cannot guard the difference.

`ui/src/e2e-coverage-fixture.test.tsx` holds the fixture to this: it recomputes
the headline from the coverpoints and crosses the way
`CoverageReport.trace_closure` does, and renders `NeverExercised` from the same
file the screenshots are served from, asserting the table is not empty. Editing
one number now fails there rather than quietly producing a payload no run can
emit.

**results (dark + light)** — the `verification` cell of the one history row:
closure `91%` → `47%`, and `2 unexercised` → `5 unexercised`. 180/179 pixels,
confined to a 38×36 box at (603, 268). No reflow — both strings are the same
width — so nothing else on the screen moves.

**dashboard (dark + light)** — the identical cell. `DashboardPage` and
`ResultsHistoryPage` both render the shared `CoverageCell`, so one fixture change
lands on both screens; the bbox is (973, 1415)–(1046, 1426). This is the coupling
the suite exists to surface, and it is the correct behaviour: a dashboard that
disagreed with the results table about the same scorecard would be the defect.

**Not moved, and checked:** `build` and `settings` are the other two screens
served from this stub, and both diffed at exactly **0** pixels — measured, not
assumed, since a fixture change reaching a screen that should not read it would
be worth knowing about. `landing`, `pricing` and `methodology` are public routes
that never call `stubApi`, so they are out of reach of this change by
construction; they were re-run and pass unchanged.

**Still owed, and not fixable from these files.** `NeverExercised` and
`CoverageWheelFor` render on **no route the suite photographs**. They live only in
`ResultsPanel`, which only `ExecutionsPage` mounts — behind an `inspect` click and
inside a collapsed `<details>` — and `/app/executions` is not in `visual.spec.ts`'s
`CONSOLE` list. So the fixture above is now correct and complete, and the coverage
table is *still* in no screenshot. Closing it needs a `run` entry in that list and
a stubbed execution + results pair, both outside this workstream's files.

One more, surfaced by typechecking the fixture against the components that read
it: `dimsFromCoverage` (`components/ds/CoverageWheel.tsx`) declares
`closure?: number`, a type in which a not-measurable coverpoint cannot exist,
while its body reads `typeof d.closure === "number" ? … : null` — written for
exactly the `null` the backend sends. Signature and body disagree and the
signature is the wrong one, so any caller passing the real payload shape has to
cast. `e2e-coverage-fixture.test.tsx` casts and says why rather than widening it
from here.

### Pending baseline changes — the `session_shape` split (P0)

Of the four baselines below, **capabilities (dark + light) have now been
regenerated** and each predicted diff was checked against the images before the
update — see the confirmation under that entry. The landing pair is unchanged
from this description. Recorded before the fact rather than after, because the
reason is what makes an updated snapshot distinguishable from an accident, and
three other workstreams are editing the same tree.

Background: the `session_shape` coverpoint counted `llm_call` spans, so an agent
that received one human message and made three tool calls was credited
`session_multi_turn`. That is multi-*step*; nobody spoke twice. The step count
moved to a new `agent_steps` coverpoint, and `session_shape` — which reads human
turns — is now declared `measurable: false`, because nothing in the build emits a
human turn.

**capabilities (dark + light)** — `e2e/support/capabilities.fixture.json` was
recaptured from `server/routes/capabilities.py`, which is what the comment above
`ROUTES` in `e2e/support/console.ts` has always claimed it is. It had gone stale,
which is the worst state for this fixture to be in: the two screenshots then
photograph a page the product can no longer serve, and every field the endpoint
gained is rendered by no test at all. The page grows, so expect a taller capture
plus reflow — **confirmed: 1280×3546 → 1280×3786**, and every item below was
located in the captured image before the baseline was replaced:

- a new `agent_steps` row in the coverage table (`single_step`, `multi_step`),
  making it 6 rows for the baseline model instead of 5 — and the "coverage
  dimensions" `Count` at the top moves 13 → 15, since it sums the baseline and
  fitted coverpoint lists;
- three new `not_covered` list items — no simulated environment or fault
  injection, no simulated user, no resumed sessions. These are the longest
  entries on the page and add roughly a screen of height on their own;
- a longer `cov.baseline.limits` string, which is the `Section` subtitle, so the
  heading block above the coverage table gets taller;
- `session_shape` now publishes `measurable: false`, its reason, and the waiver
  on its `resumed_with_memory` bin. **`CapabilitiesPage.tsx` does not render any
  of that yet** — it still draws `session_shape` as an ordinary row of bins, so
  the endpoint is honest and the page is not. That is a live gap, not a resolved
  one, and it will change these baselines a second time when it is closed.

Recapture recipe (byte-exact against the committed file, so the diff is the
endpoint's change and nothing else — verified by round-tripping the previous
fixture through it):

```
uv run python -c "import json, pathlib; \
  from agenttic.server.routes.capabilities import capabilities; \
  pathlib.Path('ui/e2e/support/capabilities.fixture.json') \
    .write_text(json.dumps(capabilities(), indent=2))"
```

Re-run before the baseline was regenerated: the committed fixture is **byte-
identical** to what the endpoint serves today, so these two baselines are a
photograph of the current page and the diff above is the endpoint's change and
nothing else. The `CapabilitiesPage.tsx` gap in the last bullet is **still open** —
`session_shape` is still drawn as an ordinary row of three bins it claims to
detect, and the "coverage dimensions" `Count` still totals 15 with it included
twice. The endpoint ships `measurable: false` and `counts_toward_closure: false`
and the page discards both, which on the page headed "not a brochure" is an
over-report of the verification surface.

**landing (dark + light)** — two deliberate changes in the "Why we said no"
section, both of them the page ceasing to contradict itself:

- `LANDING_WHEEL`'s `session_shape` entry goes `0.333` → `null`, so that sector
  redraws from a filled wedge plus gap to a hatched "not measured" sector, and
  its spoke value text goes `33%` → `not measured`. Four sectors are hatched
  where three were. The wheel appears three times on the route (`#why-refused`,
  `#see`, `#cover`), so all three move; the `#cover` instance is `compact`, which
  drops the spoke labels, so only its geometry changes.
- the copy naming "a service times out, a customer pushes back" is replaced with
  the dimensions the engine actually reads off a recording. The old sentence
  enumerated fault injection and a simulated user, neither of which exists —
  directly contradicted by the `not_covered` disclosures on the capabilities page
  in the same product. Line count is unchanged, so expect reflow within the
  paragraph rather than a height change.

Not changed, and owed: the hub figure stays `0.222` and `REFUSAL_REASONS` still
says "Only 22%". That was this run's real closure under baseline **v2**, which
averaged `session_shape` into the mean; under v3 the same traces report a
different figure, and the wheel has no `agent_steps` value at all because the
coverpoint postdates the capture. The traces are not in the tree, so neither
number can be recomputed here, and inventing them is not the alternative — the
whole wheel needs recapturing against a v3 run.

## capabilities (dark + light), recaptured again — P4 made a disclosure false

`not_covered`'s first harness entry read *"a simulated environment — there is no
world the agent acts in and no fault injection. Nothing here makes a service time
out, return a 500, or refuse a write."* P4 gave the platform a fault injector
(`agenttic.scenario.faults`) that stages all five declared `tool_condition`s on a
named call, so the sentence stopped being true the moment `agenttic cdv` could
run an agent through it. A page whose whole claim is that it is enumerated rather
than written cannot carry a disclosure the product has outgrown, in either
direction: an over-claim misleads, and an under-claim that has quietly become
false is the same defect wearing a modest hat.

The entry is now NARROWER rather than deleted, because the standard run path —
a stored suite through `run_standard`, which is what a customer runs — still has
no world and no fault injection at all. It names the path it is about, names
`agenttic cdv` as the exception, and states that exception's own three limits
(one offline retail world of eight tools; faults only on tools this platform
executes, so a black-box agent calling its own tools cannot be injected; a bin
credited only where the injector stamped the call it failed).

Consequence for these two baselines: the entry is roughly twice as long, so the
"What we don't test" list grows by about a line and a half and everything below
it on the page shifts down. Nothing else in the payload moved — the fixture was
recaptured with the recipe above in the same change, and the remaining diff is
that one string. The `CapabilitiesPage.tsx` `session_shape` gap noted above is
untouched and still open.

## capabilities (dark + light), recaptured — five property sentences moved and a
## new `scope` block appeared

Two changes to the endpoint land in these baselines at once, and both are the
same kind: a claim the product had outgrown.

**1. Five assertion property sentences changed, because what they check
changed.** The multi-turn work rescoped three first-event-only properties
(`never_tool_call_after_final_output`, `never_pii_after_redaction`,
`never_cross_tenant_identifiers`) and two more that had the same defect in the
other direction (`always_irreversible_action_confirmed`,
`never_repeated_identical_tool_call`). Each `property` string on the
capabilities page is the sentence the assertion actually verifies, so leaving
the old wording would have printed a claim stronger — or in the confirmation
case, weaker — than what is checked. The three that grew a turn clause are
longer by a few words; `never_pii_after_redaction` grew a second clause and will
wrap.

**2. A new `deterministic_checks.scope` block.** 62 of the 78 registered checks
read `final_output` and never `trace.spans`, so on a multi-turn session they
grade the last message and nothing before it — a secret disclosed at turn 3 and
not repeated at the end is scored clean by every one of them. The count is
computed from the live registry by source introspection rather than written
down, so it falls on its own as checks are made turn-aware; a hardcoded figure
would rot into exactly the false disclosure this page exists to avoid. A
matching `not_covered` entry names the limit and points at the block rather than
restating the number, so the two cannot disagree.

Consequence for these two baselines: the "What we test" section gains a small
scope readout under the deterministic-checks count, and "What we don't test"
gains one entry, so everything below shifts down by roughly two lines. The
assertion list reflows where the sentences grew. The fixture was recaptured with
the recipe in `ui/e2e/support/console.ts` in the same change, so the remaining
diff is those strings and the new block and nothing else.

Still open and untouched: the `CapabilitiesPage.tsx` `session_shape` gap and the
stale hub figure noted above.

## capabilities (dark + light), recaptured once more — `session_shape`'s reason
## had become false

The `session_shape` coverpoint's `not_measurable_reason` opened with *"nothing
emits a `user_turn` span"*. `scenario/session.py` now does, and a session driven
by `scenario/user.py` produces several, so the first clause of a customer-facing
disclosure had quietly stopped being true. On a page whose entire claim is that
it enumerates rather than asserts, a disclosure that has outgrown its own wording
is the same defect as an over-claim — it just fails in the modest direction.

The flag itself did NOT change, and the new wording says why: measurability is
declared per MODEL rather than per sample, so one instrumented batch cannot speak
for an uninstrumented one, and the standard run path — a stored suite through
`run_standard`, which is what a customer runs — still emits no turn markers at
all. Reporting `single_turn` for those traces would credit absence of
instrumentation as evidence of a single-turn session.

Recorded alongside it, because it is the reason the wording is careful rather
than triumphant: the predicate and the reason string still disagree.
`session_single_turn` is `_human_turns(trace) <= 1`, which is TRUE for a trace
with no turn markers, while the reason string says such a trace is exactly not
that. The flag hides the disagreement — an excluded coverpoint's predicate never
reaches a number — and tightening the predicate to `== 1` was tried and reverted,
because with neither bin firing the trace lands in `other`, whose drift reads as
"the model is missing a dimension" when the model is fine and the run is
uninstrumented. The real fix is per-sample measurability, which does not exist
yet; it is written down in `extractors._single` rather than resolved by changing
whichever half was cheaper.

Consequence for these two baselines: one disclosure string in the coverage
section grows by about three lines and wraps, so content below it shifts down.
The fixture was recaptured with the recipe in `ui/e2e/support/console.ts` in the
same change.

## Twelve baselines — a nav row, a page that grew, and a figure that was withdrawn

Three unrelated causes landed in one recapture. Separating them here because a
single "regenerated the baselines" line is how an unintended pixel change gets
laundered through an intended one.

**1. Ten console baselines — the sidebar gained a row.** `Scenario runs`
(`/app/scenarios`) was added to the `Issues` group, beside `What we test`. The
two belong together and say different things: `capabilities` enumerates what the
engine CAN exercise, `scenarios` shows what one run actually did — the ticket,
the staged faults and their four fates, what moved in the world, and which bins
the trace exhibited. Every console screenshot is `fullPage` and includes the
sidebar, so all five screens in both themes shift by exactly one nav row. Nothing
else on those pages moved.

Until this row existed the screen was reachable only from `/engine`'s call to
action or by typing the URL, while `EnginePage` asserted in two places that the
console "renders the same stored run in full" — true of the code and false of
anything a user could reach.

**2. capabilities (dark + light) — the page grew 3826px to 3979px.** Not a
restyle: `not_covered` gained entries. The page now also discloses that earlier
turns of a session are outside most deterministic checks, and that harness
enforcement cannot be measured for an agent whose tool loop we do not run. Both
are absences the product previously did not admit to on the one page that
promises to enumerate its own limits. A page that gets taller because it confesses
more is the direction this baseline is supposed to move.

**3. landing (dark + light) — a published figure was withdrawn.** The coverage
wheel carried `session_shape: 0.333`. That number cannot exist and never could:
the coverpoint is declared `measurable=False`, so no run emits a figure for it.
It is now `null` and draws hatched, and the caption moved from "Five were tried a
little" to "Four were tried a little, the other four are not measured at all".

The same defect was found and fixed in the engine in the same change — the CLI
was crediting `session_shape:single_turn` as exhibited coverage off traces with
zero `user_turn` spans, because it filtered bins on the raw `trace_hits` counter
instead of `countable()`/`exhibited()`. A fabricated number on the marketing page
and a fabricated bin in the evidence store were one bug wearing two hats.

Recaptured with `npx playwright test visual.spec.ts --update-snapshots`, then
re-run clean: 22 passed. No baseline was deleted, and no test assertion was
weakened to make a screenshot match.
