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


# --------------------------------------------------------------------------- #
# Classifier metrics — what both eval sources prescribe for validating a judge.
# --------------------------------------------------------------------------- #
#
# "Validate the judge against a human-labeled gold set the same way you would
# validate any classifier (true positive rate, true negative rate, Cohen's
# kappa)", with the gate stated as kappa >= ~0.6 AND a TNR high enough that it
# actually catches failures.
#
# We report Krippendorff's alpha (three-point) and exact-match (binary). Those
# are not wrong, but they share a blind spot the sources name explicitly: on a
# CLASS-IMBALANCED sample, raw agreement looks excellent for a judge that simply
# says PASS to everything. Kappa corrects for agreement expected by chance, and
# per-class recall shows WHICH class the judge is failing — the one thing a
# single agreement number cannot show.
#
# Reported ALONGSIDE the existing figures, never replacing them: changing the
# calibration metric would move a published number, and that is a decision to
# take deliberately rather than as a side effect of adding one.


def _binarise(score: float, *, threshold: float = 0.5) -> bool:
    """PASS iff at or above the threshold. Three-point 0.5 counts as PASS."""
    return score >= threshold


def confusion(pairs: list[tuple[float, float]], *,
              threshold: float = 0.5) -> dict:
    """Judge vs human as a 2x2, the shape every classifier metric needs.

    ``pairs`` is (judge_score, human_score), matching `calibration.py`.
    """
    tp = fp = tn = fn = 0
    for judge, human in pairs:
        j, h = _binarise(judge, threshold=threshold), _binarise(human, threshold=threshold)
        if h and j:
            tp += 1
        elif h and not j:
            fn += 1
        elif not h and j:
            fp += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "n": len(pairs)}


def cohens_kappa(pairs: list[tuple[float, float]], *,
                 threshold: float = 0.5) -> float | None:
    """Agreement corrected for agreement expected by chance.

    ``None`` when it is undefined — fewer than two items, or one rater used a
    single class throughout, where chance agreement is 1.0 and kappa is 0/0.
    That case is NOT reported as 0.0: "undefined" and "no better than chance"
    are different findings, and a judge that says PASS to everything on an
    all-PASS sample produces exactly it.
    """
    n = len(pairs)
    if n < 2:
        return None
    c = confusion(pairs, threshold=threshold)
    observed = (c["tp"] + c["tn"]) / n
    judge_pass = (c["tp"] + c["fp"]) / n
    human_pass = (c["tp"] + c["fn"]) / n
    expected = judge_pass * human_pass + (1 - judge_pass) * (1 - human_pass)
    if abs(1.0 - expected) < 1e-12:
        return None
    return (observed - expected) / (1 - expected)


def per_class_recall(pairs: list[tuple[float, float]], *,
                     threshold: float = 0.5) -> dict:
    """TPR and TNR — recall on each class separately.

    TNR is the one that matters most here and the one a single agreement figure
    hides: it is the fraction of genuinely FAILING items the judge caught. A
    lenient judge scores a high TPR and a near-zero TNR while its overall
    agreement still looks respectable, because most items pass.

    ``None`` rather than 0.0 when a class is absent — you cannot measure recall
    on failures you never sampled, and calling that 0.0 would report a corpus
    gap as a judge defect.
    """
    c = confusion(pairs, threshold=threshold)
    pos, neg = c["tp"] + c["fn"], c["tn"] + c["fp"]
    return {
        "tpr": (c["tp"] / pos) if pos else None,
        "tnr": (c["tn"] / neg) if neg else None,
        "n_pass_items": pos,
        "n_fail_items": neg,
        "note": ("no FAILING item in the sample, so TNR is unmeasurable — the "
                 "judge has never been shown a failure to catch"
                 if not neg else
                 "no PASSING item in the sample, so TPR is unmeasurable"
                 if not pos else ""),
    }


def classifier_report(pairs: list[tuple[float, float]], *,
                      threshold: float = 0.5, kappa_gate: float = 0.6) -> dict:
    """The judge validated as a classifier: kappa + per-class recall + verdict.

    The verdict deliberately refuses to be a single number. A judge can clear the
    kappa gate and still be useless on the class you care about, so a missing
    TNR is reported as *unmeasurable* rather than folded away.
    """
    k = cohens_kappa(pairs, threshold=threshold)
    recall = per_class_recall(pairs, threshold=threshold)
    blockers = []
    if k is None:
        blockers.append(
            "kappa is undefined — one rater used a single class throughout, "
            "which is what a judge that never says FAIL produces")
    elif k < kappa_gate:
        blockers.append(f"kappa {k:.2f} is below the {kappa_gate} gate")
    if recall["tnr"] is None:
        blockers.append(
            "TNR is unmeasurable: the sample contains no failing item, so "
            "nothing shows whether this judge can catch one")
    return {
        "n": len(pairs),
        "kappa": None if k is None else round(k, 4),
        "kappa_gate": kappa_gate,
        "tpr": None if recall["tpr"] is None else round(recall["tpr"], 4),
        "tnr": None if recall["tnr"] is None else round(recall["tnr"], 4),
        "confusion": confusion(pairs, threshold=threshold),
        "meets_gate": not blockers,
        "blockers": blockers,
        "note": (
            "Reported ALONGSIDE Krippendorff alpha / exact-match, not instead of "
            "them: raw agreement shares a blind spot on a class-imbalanced "
            "sample, where a judge that always says PASS looks excellent."),
    }
