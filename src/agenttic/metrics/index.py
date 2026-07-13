"""The normalized Agenttic Index — one composite score per agent across the
canonical metrics, with the component values always shown (honest rollup).

Index = weighted mean of the available component metric values, weights from the
catalog renormalised over whichever components are present (so a missing metric
doesn't silently drag the score to zero — its absence is reported instead).
"""

from __future__ import annotations

from agenttic.metrics.catalog import BY_ID, CHECK_TO_METRIC, index_weights


def rollup_metrics_from_means(per_criterion_means: dict[str, float]) -> dict[str, float]:
    """Map a scorecard's per-criterion means onto canonical metric values. The
    standard suites name each criterion after its check_ref, so the keys map
    directly; sub-checks of a metric (e.g. the four tool-call checks) are averaged."""
    buckets: dict[str, list[float]] = {}
    for crit_id, mean in per_criterion_means.items():
        mid = CHECK_TO_METRIC.get(crit_id)
        if mid:
            buckets.setdefault(mid, []).append(mean)
    return {mid: sum(vals) / len(vals) for mid, vals in buckets.items()}


def compute_index(metric_values: dict[str, float]) -> dict:
    """Combine canonical metric values (each in [0,1]) into the Agenttic Index.

    Returns the index (0-100), the renormalised weights used, and the component
    values + the metrics that were missing — so the rollup is never opaque."""
    weights = index_weights()
    present = {mid: v for mid, v in metric_values.items() if mid in weights}
    total_w = sum(weights[mid] for mid in present)
    index = (sum(present[mid] * weights[mid] for mid in present) / total_w) if total_w else 0.0
    missing = [mid for mid in weights if mid not in present]
    return {
        "index": round(100 * index, 1),
        "components": {mid: round(present[mid], 4) for mid in present},
        "weights_used": {mid: round(weights[mid] / total_w, 4) for mid in present} if total_w else {},
        "missing": missing,
        "names": {mid: BY_ID[mid].name for mid in present},
    }
