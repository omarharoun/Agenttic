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
