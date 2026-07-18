/* ============================================================================
   Statistics — a Python-faithful port of src/agenttic/stats.py +
   src/agenttic/scoring/calibration.py, to 1e-9.

   These are the numbers the instruments and playground draw. The golden parity
   harness replays the same fixtures the Python engine produced and asserts
   equality, so a divergence in either implementation fails the build.

   NOTE the deliberate difference from the console's display-only src/stats.ts:
   here Wilson returns [0, 1] for n<=0 (Python's "maximal ignorance"), not
   [0, 0]. sim-core matches the engine; the console file matches its UI intent.
   ========================================================================== */

/** 95% two-sided normal quantile — matches Python stats.py `Z_95`. */
export const Z_95 = 1.96;

/** Wilson score interval for a binomial pass-rate. Both bounds clamped to
 *  [0, 1]; an empty sample returns [0, 1] (maximal ignorance). Mirrors
 *  `agenttic.stats.wilson_interval`. */
export function wilsonInterval(passes: number, n: number, z = Z_95): [number, number] {
  if (n <= 0) return [0.0, 1.0];
  const phat = passes / n;
  const denom = 1 + (z * z) / n;
  const centre = phat + (z * z) / (2 * n);
  const margin = z * Math.sqrt((phat * (1 - phat) + (z * z) / (4 * n)) / n);
  return [
    Math.max(0.0, (centre - margin) / denom),
    Math.min(1.0, (centre + margin) / denom),
  ];
}

/** Wilson lower bound — the defensible floor. Mirrors `wilson_lower_bound`. */
export function wilsonLowerBound(passes: number, n: number, z = Z_95): number {
  return wilsonInterval(passes, n, z)[0];
}

/** Exact-match agreement between paired labels (binary criteria). Mirrors
 *  `agenttic.scoring.calibration.exact_match_rate`. Caller ensures non-empty. */
export function exactMatchRate(pairs: [number, number][]): number {
  return pairs.filter(([a, b]) => a === b).length / pairs.length;
}

/** Krippendorff's alpha, interval metric, two raters with paired data. Mirrors
 *  `agenttic.scoring.calibration.krippendorff_alpha_interval`.
 *  alpha = 1 - Do/De; De=0 (zero pooled variance) returns 1.0. */
export function krippendorffAlphaInterval(pairs: [number, number][]): number {
  const n = pairs.length;
  const pooled: number[] = [];
  for (const [a, b] of pairs) { pooled.push(a, b); }
  const bigN = pooled.length;
  const s1 = pooled.reduce((acc, v) => acc + v, 0);
  const s2 = pooled.reduce((acc, v) => acc + v * v, 0);
  const de = (2 * bigN * s2 - 2 * s1 * s1) / (bigN * (bigN - 1));
  if (de === 0) return 1.0;
  const doo = pairs.reduce((acc, [a, b]) => acc + (a - b) ** 2, 0) / n;
  return 1.0 - doo / de;
}
