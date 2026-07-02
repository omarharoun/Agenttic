/* ============================================================================
   Shared statistics + number formatting for the surfaces that carry the numbers.

   The credibility thesis is "credibility of the numbers", so a headline
   percentage should never appear without the sample size and its uncertainty.
   The backend already computes a Wilson lower bound in the Training Camp track
   (src/ascore/camp/trainer.py). This ports the SAME Wilson score interval to the
   client so every scorecard / results / leaderboard number can show `n` and a
   95% interval, even when the API payload only carries the point estimate + n.

   Keep this pure (no React) so it stays unit-testable.
   ========================================================================== */

export interface WilsonInterval {
  low: number;    // 0–1 lower bound
  high: number;   // 0–1 upper bound
  phat: number;   // 0–1 point estimate (passes / n)
  n: number;
}

/** Wilson score interval for a binomial proportion (z=1.96 → 95%).
 *  Mirrors `wilson_lower_bound` in camp/trainer.py; returns both bounds. */
export function wilsonInterval(passes: number, n: number, z = 1.96): WilsonInterval {
  if (!n || n <= 0) return { low: 0, high: 0, phat: 0, n: 0 };
  const phat = passes / n;
  const denom = 1 + (z * z) / n;
  const centre = phat + (z * z) / (2 * n);
  const margin = z * Math.sqrt((phat * (1 - phat) + (z * z) / (4 * n)) / n);
  return {
    low: Math.max(0, (centre - margin) / denom),
    high: Math.min(1, (centre + margin) / denom),
    phat,
    n,
  };
}

/** Lower bound only — the number you can actually defend (matches the camp gate). */
export function wilsonLower(passes: number, n: number, z = 1.96): number {
  return wilsonInterval(passes, n, z).low;
}

/** A 0–1 rate as a whole-percent string, or "—" when unknown. */
export function pct(x: number | null | undefined, digits = 0): string {
  return x == null ? "—" : `${(x * 100).toFixed(digits)}%`;
}

/** Render a Wilson 95% interval compactly, e.g. "±  62–91%" style → "62–91%". */
export function ciLabel(iv: WilsonInterval, digits = 0): string {
  return `${(iv.low * 100).toFixed(digits)}–${(iv.high * 100).toFixed(digits)}%`;
}

/** USD, or "—" when the value is genuinely unknown (null/undefined). A real 0
 *  (e.g. a cached run) still shows $0 — only missing data becomes "—". */
export function money(x: number | null | undefined, digits = 4): string {
  return x == null ? "—" : `$${x.toFixed(digits)}`;
}

/** Latency in ms, or "n/a" when unknown. A real 0 shows "0 ms". */
export function ms(x: number | null | undefined): string {
  return x == null ? "n/a" : `${Math.round(x)} ms`;
}
