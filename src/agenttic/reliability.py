"""pass^k reliability (SPEC-7 Step 31).

The single "success rate" enterprises think they are buying overstates
reliability: τ-bench's pass^k showed agents that succeed ~50% once succeed <25%
across 8 tries. We measure reliability as *consistency*.

For a case run k independent times with j passes, the unbiased estimator of
pass^k' (the chance a fresh set of k' independent attempts all pass) is

    pass_hat_k(j, k, k') = C(j, k') / C(k, k')

(the fraction of size-k' subsets of the k trials that are all passes). The
suite-level curve averages this across cases; the pass^1 → pass^k gap is the
flakiness number.
"""

from __future__ import annotations

from math import comb

#: reliability points reported when k permits (τ-bench convention)
CURVE_KS = (1, 2, 4, 8)
#: reliability claims (certificates, marketing) require at least this many trials
CERT_MIN_TRIALS = 4


class ReliabilityError(RuntimeError):
    """A reliability claim was requested with too few trials (Hard Rule 34)."""


def is_certification_grade(k: int) -> bool:
    """True when k trials suffice for a reliability claim (k >= 4)."""
    return k >= CERT_MIN_TRIALS


def require_certification_grade(k: int) -> None:
    """Raise unless k supports a reliability claim (SPEC-7 34)."""
    if not is_certification_grade(k):
        raise ReliabilityError(
            f"certification-grade reliability requires k >= {CERT_MIN_TRIALS} trials; "
            f"got k={k}. Re-run with more trials, or issue a single-trial certificate.")


def pass_hat_k(j: int, k: int, kp: int) -> float:
    """Unbiased pass^k' estimator: C(j, k') / C(k, k'). j passing of k trials."""
    if k < 1 or kp < 1 or kp > k:
        raise ValueError(f"require 1 <= k'({kp}) <= k({k})")
    if j < kp:
        return 0.0            # C(j, k') = 0 — can't draw k' passes from j < k'
    return comb(j, kp) / comb(k, kp)


def pass_k_curve(trials: dict[str, list[bool]], ks: tuple[int, ...] = CURVE_KS
                 ) -> dict[int, float]:
    """Suite-level pass^k' for each k' <= k, averaged over cases. `trials` maps
    each test_id to its per-trial pass/fail list (errored trials excluded by the
    caller). k is the min trials-per-case across cases."""
    usable = {t: v for t, v in trials.items() if v}
    if not usable:
        return {}
    k = min(len(v) for v in usable.values())
    curve: dict[int, float] = {}
    for kp in ks:
        if kp > k:
            continue
        vals = [pass_hat_k(sum(v[:k]), k, kp) for v in usable.values()]
        curve[kp] = round(sum(vals) / len(vals), 6)
    return curve


def flakiness_gap(curve: dict[int, float]) -> float | None:
    """pass^1 − pass^kmax: how much consistency erodes across repeated tries."""
    if not curve or 1 not in curve:
        return None
    return round(curve[1] - curve[max(curve)], 6)


def pass_k_regression(baseline_sc, candidate_sc) -> str | None:
    """SPEC-7 31 — when the learning gate is configured to gate on reliability, a
    candidate must not lower pass^kmax even if it raises pass^1. A promotion that
    widens the flakiness gap is exactly what the gate exists to catch. Returns a
    rejection reason, or None when it's fine / not measurable."""
    bc, cc = baseline_sc.pass_k_curve, candidate_sc.pass_k_curve
    if not bc or not cc:
        return None
    b = {int(x): v for x, v in bc.items()}
    c = {int(x): v for x, v in cc.items()}
    k = min(max(b), max(c))
    if k in b and k in c and c[k] < b[k] - 1e-9:
        return (f"rejected: pass^{k} reliability regressed {b[k]:.0%}->{c[k]:.0%} "
                f"(flakiness gap widened) even if the single-trial rate improved")
    return None
