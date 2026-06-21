"""Paired statistical tests for honest A/B verdicts — dependency-free.

The A/B flow runs two variants over the *same* cases scored by the *same*
rubric, so every comparison is **paired**: each case yields one (A, B) outcome.
That structure is what lets us say "B beats A" with a p-value instead of just
"B's number is higher".

Two tests, matched to the data:

* **McNemar's test** for the binary pass/fail comparison. A paired analogue of
  the chi-square test that looks only at the *discordant* pairs (A passed but B
  failed, and vice-versa) — concordant pairs carry no information about which
  variant is better. Exact (binomial) for small discordant counts; chi-square
  with continuity correction otherwise.
* **A paired bootstrap** for continuous per-criterion scores (which take values
  in {0, 0.5, 1}, so a t-test's normality assumption is shaky). We resample the
  paired differences to get a two-sided p-value and a confidence interval for
  the mean delta — no distributional assumption, fully deterministic (seeded).

Everything here is pure functions on plain numbers so the math is unit-tested in
isolation from the run machinery (see tests/test_ab_stats.py).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

ALPHA = 0.05  # significance level used across the A/B verdicts


@dataclass(frozen=True)
class McNemarResult:
    b: int            # discordant: A passed, B failed
    c: int            # discordant: A failed, B passed
    n_discordant: int
    statistic: float  # chi-square statistic (0.0 for the exact path)
    p_value: float
    test: str         # "exact" | "chi2_cc"
    significant: bool
    underpowered: bool  # too few discordant pairs to ever reach significance

    @property
    def favors(self) -> str:
        """Which variant the discordant pairs lean toward (``c`` = A-fail/B-pass
        favors B). This is the *direction*; whether it's trustworthy is
        ``significant``."""
        if self.c > self.b:
            return "B"
        if self.b > self.c:
            return "A"
        return "tie"

    def to_dict(self) -> dict:
        return {
            "b": self.b, "c": self.c, "n_discordant": self.n_discordant,
            "statistic": round(self.statistic, 4), "p_value": round(self.p_value, 6),
            "test": self.test, "significant": self.significant,
            "underpowered": self.underpowered, "favors": self.favors,
        }


def mcnemar(a_pass: list[bool], b_pass: list[bool], *, alpha: float = ALPHA
            ) -> McNemarResult:
    """Paired pass/fail test. ``a_pass``/``b_pass`` are the per-case binary
    outcomes for variant A and B, aligned by case (same index = same case).

    Discordant counts are all that matter: ``b`` = A right / B wrong, ``c`` =
    A wrong / B right. Uses the exact binomial test when the discordant total is
    small (the chi-square approximation is unreliable there), otherwise the
    continuity-corrected chi-square."""
    if len(a_pass) != len(b_pass):
        raise ValueError("a_pass and b_pass must be the same length (paired)")
    b = sum(1 for x, y in zip(a_pass, b_pass) if x and not y)
    c = sum(1 for x, y in zip(a_pass, b_pass) if y and not x)
    n = b + c

    # The smallest two-sided p reachable with n discordant pairs is 2*0.5^n
    # (everything on one side). If that already exceeds alpha, no split of these
    # pairs could ever be called significant — say so rather than implying parity.
    underpowered = n == 0 or 2 * (0.5 ** n) > alpha

    if n == 0:
        return McNemarResult(b, c, 0, 0.0, 1.0, "exact", False, True)

    if n < 25:
        # exact two-sided binomial p around H0: P(B>A) = P(A>B) = 0.5
        k = min(b, c)
        tail = sum(math.comb(n, i) for i in range(k + 1)) * (0.5 ** n)
        p = min(1.0, 2.0 * tail)
        return McNemarResult(b, c, n, 0.0, p, "exact",
                             p < alpha, underpowered)

    stat = (abs(b - c) - 1) ** 2 / n          # continuity correction
    p = math.erfc(math.sqrt(stat / 2))        # chi-square sf, df=1
    return McNemarResult(b, c, n, stat, p, "chi2_cc", p < alpha, underpowered)


@dataclass(frozen=True)
class BootstrapResult:
    n: int
    mean_a: float
    mean_b: float
    delta: float          # mean(B) - mean(A); >0 means B scores higher
    ci_low: float
    ci_high: float
    p_value: float
    significant: bool
    direction: str        # "B" | "A" | "tie" — which variant the delta favors

    def to_dict(self) -> dict:
        return {
            "n": self.n, "mean_a": round(self.mean_a, 4),
            "mean_b": round(self.mean_b, 4), "delta": round(self.delta, 4),
            "ci_low": round(self.ci_low, 4), "ci_high": round(self.ci_high, 4),
            "p_value": round(self.p_value, 6), "significant": self.significant,
            "direction": self.direction,
        }


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def paired_bootstrap(a: list[float], b: list[float], *, alpha: float = ALPHA,
                     resamples: int = 2000, seed: int = 1234) -> BootstrapResult:
    """Two-sided paired bootstrap on the per-case differences ``b[i] - a[i]``.

    Returns the mean delta with a (1-alpha) percentile confidence interval and a
    bootstrap p-value for H0: mean difference = 0. Deterministic for a given
    seed so the verdict is reproducible. ``a``/``b`` are aligned per-case
    criterion scores."""
    if len(a) != len(b):
        raise ValueError("a and b must be the same length (paired)")
    n = len(a)
    diffs = [bi - ai for ai, bi in zip(a, b)]
    mean_a = sum(a) / n if n else 0.0
    mean_b = sum(b) / n if n else 0.0
    delta = mean_b - mean_a
    direction = "B" if delta > 0 else ("A" if delta < 0 else "tie")

    if n < 2 or all(d == diffs[0] for d in diffs):
        # constant differences (incl. all-zero): nothing to resample over
        p = 1.0 if delta == 0 else (0.0 if n >= 2 else 1.0)
        return BootstrapResult(n, mean_a, mean_b, delta, delta, delta, p,
                               p < alpha and n >= 2, direction)

    rng = random.Random(seed)
    centered = [d - delta for d in diffs]     # simulate the null (mean 0)
    boot_means: list[float] = []
    null_means: list[float] = []
    for _ in range(resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        boot_means.append(sum(diffs[i] for i in idx) / n)
        null_means.append(sum(centered[i] for i in idx) / n)
    boot_means.sort()
    ci_low = _percentile(boot_means, alpha / 2)
    ci_high = _percentile(boot_means, 1 - alpha / 2)
    # two-sided: how often a null resample is at least as extreme as observed
    extreme = sum(1 for m in null_means if abs(m) >= abs(delta) - 1e-12)
    p = (extreme + 1) / (resamples + 1)
    return BootstrapResult(n, mean_a, mean_b, delta, ci_low, ci_high, p,
                           p < alpha, direction)
