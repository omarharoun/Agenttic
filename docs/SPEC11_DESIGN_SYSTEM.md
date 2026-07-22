# SPEC-11 — Design System Unification (build record)

Two artifacts expressed the "Chronometer" language independently — the console
(`ui/`, SPEC-4) and the marketing landing. This forced them onto one token system
and one set of shared components, so the provenance badge a prospect sees on the
homepage is literally the same component an operator sees in the scorecard.

## Steps → deliverables

| Step | Milestone | What shipped |
| --- | --- | --- |
| 50 | M32 | `ui/src/design/tokens.css` — the ONE token source; canonical `--score-*` vocabulary; `tokens.ts` mirror; `design/RECONCILIATION.md` audit trail; `scripts/check-tokens.mjs` raw-hex lint (Hard Rule 47) wired into `build`. |
| 51 | M33 | `ui/src/components/ds/` — one token-only implementation each of ProvenanceBadge, ScoreValue, ScorecardCard, Button, Eyebrow, SectionHeading, CodeBlock, StatTile, ComparisonTable, FaqItem, EscapementMark. `ProvenanceBadge` wired into the console (ResultsPanel). 14 tests + single-implementation + token-wiring guards (Hard Rule 48). |
| 52 | M34 | `pages/LandingPage.tsx` rebuilt from the shared components (was bespoke `.agx` markup). Real interactivity (assistant picker, install/eval/mcp tabs, copy, native FAQ). `see-it` uses the SAME `ScorecardCard` as the console. Social proof gated behind `SHOW_SOCIAL_PROOF` (off until real, Hard Rule 49). Public route: SiteNav only, no authed data. |
| 53 | M35 | Bundle-budget gate (`check-bundle.mjs`, 150 KB gz; AppShell must stay lazy — currently 95.7 KB). `public/llms.txt` mirror at `/llms.txt`. Reduced-motion, single-h1, aria-hidden dial, keyboard-operable controls, 360px overflow guard — asserted in a structural a11y test + the Node lint. |

## Hard rules added (47–50)

47. One token source of truth — the landing and console never define colours,
    type, or spacing independently again (`design/tokens.css`, lint-enforced).
48. Shared product components (ProvenanceBadge, ScorecardCard, ScoreValue) have
    exactly one implementation used by every surface.
49. The landing ships no fabricated proof — every star/download/quote/logo is
    flag-gated off until bound to real data.
50. The marketing route is a real route in the design system, never a pasted
    static file.

## Honest scope

- **Theme default:** both themes are reconciled and the landing renders in each;
  a per-route "default to light" for the public surface is deferred (it touches
  the global theme bootstrap for all public pages) — not blocking.
- **Full axe + visual-regression** are browser-runner (Playwright) CI gates and
  are NOT set up in this environment. Everything checkable without a browser
  (bundle budget, structural a11y, reduced-motion, overflow, token lint) is gated
  and passing.
- The old `.agx` landing CSS in `theme.css` is now dead (the route no longer uses
  it); a cleanup sweep is a safe follow-up.

## Verification

Build green (`lint:tokens` incl. bar checks + `tsc` + `vite-react-ssg build` +
`check-bundle`), **121 ui tests pass**, token lint clean across 46 files, landing
prerenders entirely from `ds-*` components with social proof absent and zero
fabricated figures.
