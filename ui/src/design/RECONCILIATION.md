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
three consecutive runs that diffed at zero, so the headroom absorbs antialiasing
without hiding a real change.

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
