/* Typed mirror of design/tokens.css for JS/TS consumers (SPEC-11 Step 50).
 *
 * These are NOT hard-coded colours — each value is a `var(--token)` reference,
 * so JS consumers resolve to the same single source of truth as CSS and follow
 * the active theme. Import these instead of writing raw hex in a component
 * (which the token lint forbids in src/pages and src/components).
 *
 *   import { score } from "../design/tokens";
 *   <span style={{ color: score.pass }}>PASS</span>
 */

/** The shared SCORE vocabulary — one meaning, one token, both themes. */
export const score = {
  pass: "var(--score-pass)",
  passSoft: "var(--score-pass-soft)",
  passBorder: "var(--score-pass-border)",
  provisional: "var(--score-provisional)",
  provisionalSoft: "var(--score-provisional-soft)",
  provisionalBorder: "var(--score-provisional-border)",
  deterministic: "var(--score-deterministic)",
  deterministicSoft: "var(--score-deterministic-soft)",
  deterministicBorder: "var(--score-deterministic-border)",
  fail: "var(--score-fail)",
  failSoft: "var(--score-fail-soft)",
  failBorder: "var(--score-fail-border)",
} as const;

/** How a criterion was measured — the real provenance model the badge renders. */
export type Provenance = "deterministic" | "calibrated" | "provisional" | "fail";

/** Map a provenance to its score token colour + soft/border companions. */
export function provenanceTokens(p: Provenance) {
  switch (p) {
    case "deterministic":
      return { color: score.deterministic, soft: score.deterministicSoft, border: score.deterministicBorder };
    case "calibrated":
      return { color: score.pass, soft: score.passSoft, border: score.passBorder };
    case "provisional":
      return { color: score.provisional, soft: score.provisionalSoft, border: score.provisionalBorder };
    case "fail":
      return { color: score.fail, soft: score.failSoft, border: score.failBorder };
  }
}

/** Core surface / ink / accent tokens for JS consumers. */
export const color = {
  bg: "var(--bg)",
  panel: "var(--panel)",
  border: "var(--border)",
  text: "var(--text)",
  muted: "var(--muted)",
  faint: "var(--faint)",
  accent: "var(--accent)",
  accentHover: "var(--accent-hover)",
  live: "var(--live)",
} as const;

export const font = {
  ui: "var(--font-ui)",
  serif: "var(--font-serif)",
  mono: "var(--font-mono)",
} as const;

export const radius = {
  xs: "var(--r-xs)", sm: "var(--r-sm)", md: "var(--r-md)",
  lg: "var(--r-lg)", xl: "var(--r-xl)", pill: "var(--r-pill)",
} as const;

export const motion = {
  ease: "var(--ease-escape)",
  fast: "var(--dur-fast)",
  gauge: "var(--dur-gauge)",
} as const;
