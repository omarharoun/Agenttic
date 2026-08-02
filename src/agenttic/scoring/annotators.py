"""Several humans per item — so judge-vs-human agreement can be READ.

The shipped calibration corpus carries ONE ``human_score`` per record, and that
single number is why the recorded 1.0 agreement means nothing. With one label
you cannot tell these two situations apart:

    the judge is wrong                 <-- a defect in the judge
    the item is genuinely ambiguous    <-- a defect in the ITEM

and they call for opposite responses. Fixing a judge that disagrees on an
ambiguous item makes it worse.

THE CEILING
-----------
Human-human agreement is the ceiling. If two careful annotators agree 0.62 on a
criterion, a judge scoring 0.62 against them is performing AT the limit the task
allows, not failing — and a judge scoring 0.95 against a single annotator has
probably learned that annotator, not the criterion. So agreement is reported as
a FRACTION OF THE CEILING as well as raw, and a raw figure without a ceiling is
labelled as what it is: unscoped.

This module only supplies the ceiling and the arithmetic. It does not promote
anything: ``demonstrated_calibrated_judge`` remains the sole gate, and it needs
a real study this module makes possible rather than performs.

BACK COMPATIBILITY
------------------
``human_score: 0.5`` (a float) and ``human_scores: [{annotator, score}]`` are
both accepted. The single-label form keeps working exactly as before and is
reported as ``n_annotators: 1`` with no ceiling — visibly unscoped, rather than
silently treated as gold.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass

#: Scores allowed on a three-point criterion (Hard Rule 3).
THREE_POINT = (0.0, 0.5, 1.0)


@dataclass(frozen=True)
class Label:
    """One annotator's score for one (record, criterion)."""

    annotator: str
    score: float


def labels_of(record: dict) -> list[Label]:
    """Every human label on a corpus record, old or new shape.

    Raises nothing on the legacy shape: a corpus written before multi-annotation
    must keep loading, or the fix breaks the thing it is meant to improve.
    """
    multi = record.get("human_scores")
    if isinstance(multi, list) and multi:
        out = []
        for i, item in enumerate(multi):
            if isinstance(item, dict):
                out.append(Label(str(item.get("annotator") or f"a{i+1}"),
                                 float(item["score"])))
            else:                                   # bare list of numbers
                out.append(Label(f"a{i+1}", float(item)))
        return out
    if record.get("human_score") is not None:
        return [Label("a1", float(record["human_score"]))]
    return []


def consensus(labels: list[Label], scale: str = "three_point") -> float | None:
    """The single number to compare a judge against. ``None`` when there is none.

    Median rather than mean: on a three-point scale a mean of two disagreeing
    annotators invents 0.25, a value the scale does not contain and the judge
    can never produce, which would make disagreement structurally unfixable.
    An EVEN split has no consensus and returns None — the honest answer, and the
    signal that the item needs a third annotator rather than a verdict.
    """
    if not labels:
        return None
    scores = sorted(x.score for x in labels)
    if len(scores) % 2 == 1:
        return scores[len(scores) // 2]
    lo, hi = scores[len(scores) // 2 - 1], scores[len(scores) // 2]
    if lo == hi:
        return lo
    return None                       # a tie is not a consensus


def pairwise_agreement(labels: list[Label], *, tolerance: float = 0.0) -> float | None:
    """Observed agreement between annotators: the fraction of pairs that match.

    ``None`` for fewer than two annotators — the case the old corpus is in, and
    reporting 1.0 there ("the one annotator agrees with itself") would be the
    exact overclaim this module exists to prevent.
    """
    if len(labels) < 2:
        return None
    pairs = list(itertools.combinations(labels, 2))
    hits = sum(1 for a, b in pairs if abs(a.score - b.score) <= tolerance)
    return hits / len(pairs)


def ceiling(records: list[dict], criterion_id: str, *,
            tolerance: float = 0.0) -> dict:
    """Human-human agreement for one criterion — the ceiling for the judge."""
    per_item, n_multi, ambiguous = [], 0, []
    for rec in records:
        if rec.get("criterion_id") != criterion_id:
            continue
        labs = labels_of(rec)
        if len(labs) < 2:
            continue
        n_multi += 1
        agr = pairwise_agreement(labs, tolerance=tolerance)
        per_item.append(agr or 0.0)
        if consensus(labs) is None:
            ambiguous.append(rec.get("record_id", "?"))
    if not per_item:
        return {
            "criterion_id": criterion_id, "n_multi_annotated": 0,
            "ceiling": None, "ambiguous_items": [],
            "note": ("no item for this criterion carries two or more human "
                     "labels, so there is no ceiling: a judge-vs-human figure "
                     "here is UNSCOPED — it cannot separate a wrong judge from "
                     "an ambiguous item"),
        }
    c = statistics.fmean(per_item)
    return {
        "criterion_id": criterion_id,
        "n_multi_annotated": n_multi,
        "ceiling": round(c, 4),
        "ambiguous_items": ambiguous,
        "note": (
            f"human annotators agree {c:.1%} of the time on this criterion. A "
            f"judge scoring near {c:.1%} is at the limit the task allows, not "
            "failing; a judge scoring far ABOVE it has probably learned an "
            "annotator rather than the criterion."
            + (f" {len(ambiguous)} item(s) have no consensus and should get a "
               "third label rather than a verdict." if ambiguous else "")),
    }


def scoped_agreement(raw_agreement: float | None,
                     ceiling_value: float | None) -> dict:
    """Judge agreement expressed against the ceiling, never on its own.

    A raw number with no ceiling is returned labelled ``unscoped`` rather than
    dressed up — the same refusal the coverage layer makes for a closure figure
    with no stated population.
    """
    if raw_agreement is None:
        return {"raw": None, "ceiling": ceiling_value, "scoped": None,
                "status": "not_measured",
                "note": "the judge was not run over any labelled item"}
    if not ceiling_value:
        return {"raw": round(raw_agreement, 4), "ceiling": None, "scoped": None,
                "status": "unscoped",
                "note": ("no human-human ceiling exists for this criterion, so "
                         "this figure cannot say whether the judge is wrong or "
                         "the items are ambiguous. Add a second annotator.")}
    scoped = raw_agreement / ceiling_value if ceiling_value else None
    return {
        "raw": round(raw_agreement, 4),
        "ceiling": round(ceiling_value, 4),
        "scoped": round(scoped, 4) if scoped is not None else None,
        "status": "scoped",
        "note": (
            f"the judge matches the human consensus {raw_agreement:.1%} of the "
            f"time, against a human-human ceiling of {ceiling_value:.1%} "
            f"({scoped:.0%} of the ceiling)."
            + (" Above the ceiling means it agrees with the consensus more "
               "often than the annotators agree with each other — treat that as "
               "a reason to inspect the items, not as a better judge."
               if scoped and scoped > 1.0 else "")),
    }


def corpus_health(records: list[dict]) -> dict:
    """Is this corpus capable of supporting a calibration claim at all?

    Reported before any judge is run, because a corpus that cannot discriminate
    makes the run worthless however it comes out — and because "we ran it and it
    agreed" on easy items is the failure this whole module addresses.
    """
    by_crit: dict[str, list[dict]] = {}
    for rec in records:
        cid = rec.get("criterion_id")
        if cid:
            by_crit.setdefault(cid, []).append(rec)

    out = {}
    for cid, recs in by_crit.items():
        labs = [labels_of(r) for r in recs]
        multi = sum(1 for L in labs if len(L) >= 2)
        cons = [consensus(L) for L in labs if L]
        spread = len({c for c in cons if c is not None})
        out[cid] = {
            "n_items": len(recs),
            "n_multi_annotated": multi,
            "distinct_consensus_values": spread,
            "discriminating": spread >= 2,
            "blockers": [
                *([] if multi else
                  ["no item has two annotators: agreement here is unscoped"]),
                *([] if spread >= 2 else
                  ["every item has the same human label, so agreement measures "
                   "nothing — a judge that always answers that value scores 1.0"]),
            ],
        }
    return out
