"""SPEC-3 Step 15.2 — the calibration set becomes a train / held-out benchmark.

Covers the deterministic seeded split (``split_labels``), the FROZEN + extend
persistence (``frozen_split`` + ``CalibrationSplitRow``, Hard Rule 15: adding
labels never reshuffles a prior assignment), and the minimum-n optimizability
guard (``optimizable``).
"""

from __future__ import annotations

from agenttic.registry.sqlite_store import CalibrationSplitRow, Registry
from agenttic.scoring.calibration import (
    HOLDOUT,
    TRAIN,
    frozen_split,
    optimizable,
    split_labels,
)

CID = "refund_correctness"
OTHER = "tone"


def make_labels(trace_ids, criterion_id=CID, score=1.0):
    return {(tid, criterion_id): score for tid in trace_ids}


# -- split_labels: deterministic ------------------------------------------- #

def test_same_seed_same_labels_identical_split():
    labels = make_labels([f"tr-{i}" for i in range(20)])
    tr1, ho1 = split_labels(labels, CID, holdout_frac=0.4, seed=1234)
    tr2, ho2 = split_labels(labels, CID, holdout_frac=0.4, seed=1234)
    assert set(tr1) == set(tr2)
    assert set(ho1) == set(ho2)
    # train and holdout partition the labels, no overlap.
    assert set(tr1).isdisjoint(set(ho1))
    assert set(tr1) | set(ho1) == set(labels)


def test_different_seed_generally_different_and_frac_respected():
    n = 40
    labels = make_labels([f"tr-{i}" for i in range(n)])
    _, ho_a = split_labels(labels, CID, holdout_frac=0.4, seed=1)
    _, ho_b = split_labels(labels, CID, holdout_frac=0.4, seed=999)
    assert set(ho_a) != set(ho_b)          # different seed => different partition
    # holdout size ~ frac * n (rounded).
    assert len(ho_a) == round(n * 0.4) == 16
    assert len(ho_b) == 16


def test_split_filters_to_one_criterion():
    labels = {**make_labels([f"tr-{i}" for i in range(10)], CID),
              **make_labels([f"tr-{i}" for i in range(10)], OTHER)}
    train, holdout = split_labels(labels, CID, holdout_frac=0.4, seed=5)
    assert all(cid == CID for (_, cid) in train)
    assert all(cid == CID for (_, cid) in holdout)
    assert len(train) + len(holdout) == 10


# -- frozen_split: FROZEN + EXTEND (Hard Rule 15) -------------------------- #

def test_frozen_split_persists_and_reuses(tmp_path):
    reg = Registry(tmp_path / "c.db")
    labels = make_labels([f"tr-{i}" for i in range(20)])
    tr1, ho1 = frozen_split(reg, labels, CID, holdout_frac=0.4, seed=1234)
    # A second call with the same labels returns the identical persisted split.
    tr2, ho2 = frozen_split(reg, labels, CID, holdout_frac=0.4, seed=1234)
    assert set(tr1) == set(tr2)
    assert set(ho1) == set(ho2)
    # And it matches the pure (unfrozen) computation on first creation.
    ptr, pho = split_labels(labels, CID, holdout_frac=0.4, seed=1234)
    assert set(tr1) == set(ptr)
    assert set(ho1) == set(pho)


def test_frozen_extend_never_reshuffles(tmp_path):
    reg = Registry(tmp_path / "c.db")
    labels = make_labels([f"tr-{i}" for i in range(20)])
    tr1, ho1 = frozen_split(reg, labels, CID, holdout_frac=0.4, seed=1234)
    side_before = {tid for (tid, _) in ho1}   # trace_ids on the holdout side

    # Add 10 NEW labels and re-call.
    more = make_labels([f"new-{i}" for i in range(10)])
    labels2 = {**labels, **more}
    tr2, ho2 = frozen_split(reg, labels2, CID, holdout_frac=0.4, seed=1234)

    assignment_before = {tid: HOLDOUT for (tid, _) in ho1}
    assignment_before.update({tid: TRAIN for (tid, _) in tr1})
    assignment_after = {tid: HOLDOUT for (tid, _) in ho2}
    assignment_after.update({tid: TRAIN for (tid, _) in tr2})

    # Every previously-assigned trace_id keeps its ORIGINAL side.
    for tid, side in assignment_before.items():
        assert assignment_after[tid] == side, f"{tid} moved sides"

    # The holdout side is a superset of before (nothing left it).
    assert side_before <= {tid for (tid, _) in ho2}
    # New trace_ids were assigned somewhere.
    new_ids = {f"new-{i}" for i in range(10)}
    assert new_ids <= set(assignment_after)
    # Total covers all labels.
    assert len(tr2) + len(ho2) == 30


def test_frozen_split_registry_round_trip(tmp_path):
    reg = Registry(tmp_path / "c.db")
    labels = make_labels([f"tr-{i}" for i in range(12)])
    frozen_split(reg, labels, CID, holdout_frac=0.5, seed=7)

    stored = reg.get_calibration_split(CID, seed=7)
    assert set(stored) == {f"tr-{i}" for i in range(12)}
    assert set(stored.values()) <= {TRAIN, HOLDOUT}
    # A different (criterion, seed) is isolated.
    assert reg.get_calibration_split(CID, seed=8) == {}
    assert reg.get_calibration_split(OTHER, seed=7) == {}


def test_calibration_split_row_round_trip(tmp_path):
    """Direct row-level round-trip: save then read back the assignment."""
    from datetime import datetime, timezone

    from sqlmodel import Session, select
    reg = Registry(tmp_path / "c.db")
    reg.save_calibration_split(CID, 42, {"a": TRAIN, "b": HOLDOUT})
    with Session(reg.engine) as s:
        rows = s.exec(select(CalibrationSplitRow).where(
            CalibrationSplitRow.criterion_id == CID,
            CalibrationSplitRow.seed == 42)).all()
    assert {r.trace_id: r.side for r in rows} == {"a": TRAIN, "b": HOLDOUT}
    assert all(isinstance(r.created_at, datetime) for r in rows)
    # Idempotent extend: re-saving the same trace_id does not duplicate/move it.
    reg.extend_calibration_split(CID, 42, {"a": HOLDOUT, "c": TRAIN})
    assert reg.get_calibration_split(CID, 42) == {
        "a": TRAIN, "b": HOLDOUT, "c": TRAIN}
    _ = timezone  # imported for clarity in the assertion above


# -- optimizable: minimum-n guard ------------------------------------------ #

def test_below_min_n_not_optimizable():
    cfg = {"judge_learning": {"min_labels": 20}}
    labels = make_labels([f"tr-{i}" for i in range(5)])
    ok, reason = optimizable(labels, CID, cfg)
    assert ok is False
    assert "insufficient" in reason
    assert "min_labels" in reason
    assert "5" in reason


def test_at_or_above_min_n_optimizable():
    cfg = {"judge_learning": {"min_labels": 20}}
    labels = make_labels([f"tr-{i}" for i in range(20)])
    ok, reason = optimizable(labels, CID, cfg)
    assert ok is True
    assert "min_labels=20" in reason


def test_min_labels_defaults_to_20():
    labels = make_labels([f"tr-{i}" for i in range(19)])
    ok, reason = optimizable(labels, CID, {})   # no judge_learning block
    assert ok is False
    assert "min_labels=20" in reason
