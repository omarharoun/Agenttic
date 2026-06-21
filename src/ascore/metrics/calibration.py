"""Calibration — Expected Calibration Error (ECE) + abstention-appropriateness.

ECE (Guo et al., 2017, "On Calibration of Modern Neural Networks"): bin
predictions by confidence, and within each bin compare mean confidence to mean
accuracy; ECE is the sample-weighted mean absolute gap. Requires the agent to
emit a confidence per prediction. When confidence is unavailable, only
abstention-appropriateness is reported (and the limitation is surfaced).
"""

from __future__ import annotations


def ece(confidences: list[float], correct: list[bool], n_bins: int = 10) -> float:
    """Expected Calibration Error in [0,1] (lower is better)."""
    if not confidences or len(confidences) != len(correct):
        raise ValueError("confidences and correct must be non-empty and equal length")
    n = len(confidences)
    bins: list[tuple[list[float], list[bool]]] = [([], []) for _ in range(n_bins)]
    for c, ok in zip(confidences, correct):
        c = min(max(float(c), 0.0), 1.0)
        idx = min(n_bins - 1, int(c * n_bins))
        bins[idx][0].append(c)
        bins[idx][1].append(bool(ok))
    total = 0.0
    for confs, oks in bins:
        if not confs:
            continue
        avg_conf = sum(confs) / len(confs)
        accuracy = sum(1 for o in oks if o) / len(oks)
        total += (len(confs) / n) * abs(avg_conf - accuracy)
    return total


def abstention_appropriateness(scores: list[float]) -> float:
    """Mean of the per-case ``abstention_correct`` scores (1 = abstained iff it
    should have). Reported when confidence isn't available for full ECE."""
    if not scores:
        return 0.0
    return sum(scores) / len(scores)
