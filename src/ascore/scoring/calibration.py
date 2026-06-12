"""Judge calibration — measure judge-vs-human agreement per criterion.

A judge without measured human agreement produces numbers, not measurements.
Criteria below the agreement threshold are flagged UNCALIBRATED and their
scores are marked provisional in every scorecard (Hard Rule 6).

Labels CSV format (``calibration/{suite_id}.csv``)::

    trace_id,criterion_id,human_score

Agreement metric: exact-match rate for binary criteria; Krippendorff's alpha
(interval metric, two raters: judge vs human) for three-point criteria.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

LabelKey = tuple[str, str]  # (trace_id, criterion_id)


def load_labels(path: str | Path) -> dict[LabelKey, float]:
    labels: dict[LabelKey, float] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            labels[(row["trace_id"], row["criterion_id"])] = float(row["human_score"])
    if not labels:
        raise ValueError(f"no labels found in {path}")
    return labels


def exact_match_rate(pairs: list[tuple[float, float]]) -> float:
    return sum(1 for a, b in pairs if a == b) / len(pairs)


def krippendorff_alpha_interval(pairs: list[tuple[float, float]]) -> float:
    """Krippendorff's alpha, interval metric, two raters with paired data.

    alpha = 1 - Do/De;  Do = mean squared judge-human difference;
    De = expected squared difference between two values drawn without
    replacement from the pooled distribution.
    """
    n = len(pairs)
    pooled = [v for p in pairs for v in p]
    big_n = len(pooled)
    s1 = sum(pooled)
    s2 = sum(v * v for v in pooled)
    de = (2 * big_n * s2 - 2 * s1 * s1) / (big_n * (big_n - 1))
    if de == 0:
        return 1.0  # zero variance and zero disagreement
    do = sum((a - b) ** 2 for a, b in pairs) / n
    return 1.0 - do / de


@dataclass(frozen=True)
class CriterionCalibration:
    criterion_id: str
    n: int
    agreement: float
    calibrated: bool


def calibration_report(
    judge_scores: list[tuple[str, str, float]],  # (trace_id, criterion_id, score)
    labels: dict[LabelKey, float],
    scales: dict[str, str],  # criterion_id -> "binary" | "three_point"
    threshold: float = 0.8,
    min_n: int = 5,
) -> dict[str, CriterionCalibration]:
    """Pair judge scores with human labels and compute per-criterion agreement.

    Criteria with fewer than ``min_n`` labeled pairs are reported but never
    considered calibrated — too little evidence either way.
    """
    pairs_by_crit: dict[str, list[tuple[float, float]]] = {}
    for trace_id, criterion_id, score in judge_scores:
        human = labels.get((trace_id, criterion_id))
        if human is not None:
            pairs_by_crit.setdefault(criterion_id, []).append((score, human))

    report: dict[str, CriterionCalibration] = {}
    for cid, pairs in pairs_by_crit.items():
        metric = (
            exact_match_rate
            if scales.get(cid, "binary") == "binary"
            else krippendorff_alpha_interval
        )
        agreement = metric(pairs)
        report[cid] = CriterionCalibration(
            criterion_id=cid,
            n=len(pairs),
            agreement=agreement,
            calibrated=(len(pairs) >= min_n and agreement >= threshold),
        )
    return report
