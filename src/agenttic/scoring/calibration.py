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
import random
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


# --------------------------------------------------------------------------- #
# Step 15.2 — the calibration set becomes a train / held-out benchmark.
#
# To learn a judge for a criterion we split its human labels into a TRAIN set
# (used to fit/select a judge config) and a HELD-OUT benchmark (used to measure
# whether the learned judge really improved — never trained on). The split is a
# deterministic seeded shuffle, mirroring ``optimizer.split_suite``: for a given
# (criterion's label set, seed) it is byte-for-byte identical across runs, so a
# learning round is reproducible and auditable.
#
# Hard Rule 15 — frozen + extend: every optimization round for a criterion must
# reuse THE SAME held-out set, or the benchmark leaks. ``frozen_split`` persists
# the assignment (trace_id -> "train"|"holdout") per (tenant, criterion, seed).
# When labels are added later it EXTENDS the split — new trace_ids are assigned
# by the same seeded rule while every prior assignment stays put — so the
# held-out benchmark never reshuffles under an optimizer's feet.
# --------------------------------------------------------------------------- #

HOLDOUT = "holdout"
TRAIN = "train"


def _trace_ids_for(labels: dict[LabelKey, float], criterion_id: str) -> list[str]:
    """Sorted, de-duplicated trace_ids that carry a label for one criterion."""
    return sorted({tid for (tid, cid) in labels if cid == criterion_id})


def _assign(trace_ids: list[str], holdout_frac: float, seed: int) -> dict[str, str]:
    """Deterministic seeded partition of trace_ids into train/holdout.

    Sorted-then-seeded-shuffle (same discipline as ``optimizer.split_suite``):
    the first ``holdout_frac`` of the shuffled ids become the held-out
    benchmark, the rest train. Stable for a given (id set, seed)."""
    ids = sorted(trace_ids)
    rng = random.Random(seed)
    shuffled = ids[:]
    rng.shuffle(shuffled)
    n_held = int(round(len(ids) * holdout_frac))
    n_held = max(0, min(n_held, len(ids)))
    held = set(shuffled[:n_held])
    return {tid: (HOLDOUT if tid in held else TRAIN) for tid in ids}


def _partition(labels: dict[LabelKey, float], criterion_id: str,
               assignment: dict[str, str]
               ) -> tuple[dict[LabelKey, float], dict[LabelKey, float]]:
    """Split this criterion's labels into (train, holdout) using ``assignment``
    (trace_id -> side). Labels for trace_ids not in the assignment are dropped
    (they belong to another criterion or aren't assigned yet)."""
    train: dict[LabelKey, float] = {}
    holdout: dict[LabelKey, float] = {}
    for (tid, cid), score in labels.items():
        if cid != criterion_id:
            continue
        side = assignment.get(tid)
        if side == HOLDOUT:
            holdout[(tid, cid)] = score
        elif side == TRAIN:
            train[(tid, cid)] = score
    return train, holdout


def split_labels(labels: dict[LabelKey, float], criterion_id: str, *,
                 holdout_frac: float = 0.4, seed: int,
                 ) -> tuple[dict[LabelKey, float], dict[LabelKey, float]]:
    """Deterministically split ONE criterion's labels into (train, holdout).

    Filters ``labels`` to ``criterion_id``, then partitions its trace_ids by a
    seeded shuffle: the first ``holdout_frac`` → holdout, the rest → train. For
    a given (criterion's label set, seed) the split is identical across runs."""
    ids = _trace_ids_for(labels, criterion_id)
    assignment = _assign(ids, holdout_frac, seed)
    return _partition(labels, criterion_id, assignment)


def frozen_split(reg, labels: dict[LabelKey, float], criterion_id: str, *,
                 holdout_frac: float = 0.4, seed: int,
                 ) -> tuple[dict[LabelKey, float], dict[LabelKey, float]]:
    """Frozen + extend (Hard Rule 15): reuse the persisted split for a criterion
    so every optimization round sees THE SAME held-out benchmark.

    * If a split exists for (criterion_id, seed), reuse the stored side for every
      trace_id it knows. Any NEW trace_ids (labels added since) are assigned by
      the SAME seeded rule — computed over the full current id set — WITHOUT
      moving existing assignments (extend, never reshuffle). The extension is
      persisted.
    * If none exists, compute via ``split_labels`` and persist it.

    Returns (train, holdout) dicts over the criterion's current labels."""
    ids = _trace_ids_for(labels, criterion_id)
    stored = reg.get_calibration_split(criterion_id, seed)

    if not stored:
        assignment = _assign(ids, holdout_frac, seed)
        if assignment:
            reg.save_calibration_split(criterion_id, seed, assignment)
        return _partition(labels, criterion_id, assignment)

    # Extend: keep every stored assignment; assign only genuinely new trace_ids.
    new_ids = [tid for tid in ids if tid not in stored]
    if new_ids:
        # Recompute the canonical seeded partition over the FULL current id set,
        # but only adopt its verdict for the new ids — stored ids never move.
        fresh = _assign(ids, holdout_frac, seed)
        additions = {tid: fresh[tid] for tid in new_ids}
        reg.extend_calibration_split(criterion_id, seed, additions)
        assignment = {**stored, **additions}
    else:
        assignment = stored
    return _partition(labels, criterion_id, assignment)


# --------------------------------------------------------------------------- #
# Minimum-n guard — a criterion with too few labels is not optimizable.
# --------------------------------------------------------------------------- #

_DEFAULT_MIN_LABELS = 20


def min_labels(cfg: dict) -> int:
    """Resolve ``judge_learning.min_labels`` (default 20) from config."""
    return int((cfg.get("judge_learning") or {}).get(
        "min_labels", _DEFAULT_MIN_LABELS))


def optimizable(labels: dict[LabelKey, float], criterion_id: str, cfg: dict
                ) -> tuple[bool, str]:
    """Whether a criterion has enough human labels to learn a judge.

    A criterion with fewer than ``judge_learning.min_labels`` TOTAL labels is
    not optimizable — too little signal to fit a judge AND hold a benchmark out.
    Returns (ok, reason); the reason is a clear message when not ok. Step 15.3's
    ``learn-judge`` calls this and reports "insufficient labels"."""
    n = len(_trace_ids_for(labels, criterion_id))
    threshold = min_labels(cfg)
    if n < threshold:
        return False, (
            f"insufficient labels: {n} < min_labels={threshold}")
    return True, f"{n} labels >= min_labels={threshold}"
