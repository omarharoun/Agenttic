"""Prepare the judge corpus for a study a second annotator can actually run.

The labelling itself cannot be automated here, and the reason is not effort. A
model labelling data for a judge-vs-human agreement study turns it into
judge-vs-judge — the exact independence failure the playbook opens with:

    an evaluation is only as trustworthy as the independence between its data
    and the system under test.

The corpus is already compromised on that axis: all 15 records carry ONE
``human_score``, authored by the same people who wrote the judge prompt. Adding
model labels would look like progress while making it worse.

Everything AROUND the labelling is automatable, and none of it is:

* the corpus is still in the single-label shape, so a second annotator has
  nowhere to put a score;
* there are no written instructions, so two annotators would not be labelling
  the same question;
* the 15 items are clear-cut, so even a perfect study would measure little —
  `annotators.corpus_health` reports the label spread, and a corpus that cannot
  discriminate makes the run worthless however it comes out.

This module does those three. It emits a labelling sheet with the judge's own
scores REMOVED, converts the corpus to the multi-annotator shape without
touching the existing labels, and merges returned sheets back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from agenttic.scoring.annotators import labels_of

#: Fields an annotator must see to label an item, and nothing more. The judge's
#: score and any existing human label are DELIBERATELY excluded: showing either
#: anchors the annotator to it, and an anchored second label is not an
#: independent one — it is the first label with a witness.
SHEET_FIELDS = ("record_id", "criterion_id", "scale", "description", "anchors",
                "task_description", "reference_context", "final_output")


@dataclass
class SheetItem:
    record_id: str
    criterion_id: str
    payload: dict


def labelling_sheet(records: list[dict], *, annotator: str) -> dict:
    """A blind labelling sheet: the item, the criterion, and nowhere to peek.

    Blind is the whole point. If the sheet carried the existing `human_score`,
    the second annotator would be confirming a label rather than producing one,
    and the human-human ceiling computed from it would be an overestimate — which
    would then make the judge look worse than it is against a ceiling that is
    too high.
    """
    items = []
    for rec in records:
        if not rec.get("criterion_id"):
            continue                       # the corpus header comment row
        items.append({
            **{k: rec.get(k) for k in SHEET_FIELDS if rec.get(k) is not None},
            "score": None,                 # <- the annotator fills this
            "note": "",
        })
    return {
        "annotator": annotator,
        "instructions": INSTRUCTIONS,
        "items": items,
        "n_items": len(items),
    }


#: Given to every annotator verbatim. Two people labelling different questions
#: produce disagreement that measures the instructions, not the items.
INSTRUCTIONS = """\
You are scoring ONE criterion at a time. Do not score overall quality.

For each item you are given the task, any reference context, the agent's output,
the criterion, and its pass/fail anchors. Score ONLY that criterion.

Scales:
  binary        1 = the criterion is met, 0 = it is not.
  three_point   1 = fully met, 0.5 = partially met, 0 = not met.

Rules that matter for the study:

1. Judge against the ANCHORS, not against your own taste. If the anchors do not
   settle it, that is a finding — say so in the note rather than picking.
2. Judge the output only against what it was GIVEN. Do not use outside
   knowledge, and do not reward an answer for being true if the context does not
   support it.
3. Length is not quality. A short answer that meets the criterion scores the
   same as a long one that does.
4. If you genuinely cannot decide, leave `score` null and write why. An honest
   "cannot decide" is worth more than a coin flip: two annotators splitting on
   an item tells us the ITEM is ambiguous, which is a different problem from the
   judge being wrong, and we need to be able to tell those apart.
5. Do not confer with the other annotator. Independence is the measurement.
"""


def to_multi_annotator(records: list[dict], *,
                       existing_annotator: str = "a1") -> list[dict]:
    """Convert the single-label corpus to the multi-annotator shape.

    Lossless and idempotent: an existing ``human_score`` becomes the first entry
    in ``human_scores`` and is left in place too, so anything still reading the
    old field keeps working. A record already converted is returned untouched.
    """
    out = []
    for rec in records:
        if not rec.get("criterion_id"):
            out.append(dict(rec))
            continue
        r = dict(rec)
        if isinstance(r.get("human_scores"), list) and r["human_scores"]:
            out.append(r)
            continue
        if r.get("human_score") is not None:
            r["human_scores"] = [{"annotator": existing_annotator,
                                  "score": float(r["human_score"])}]
        out.append(r)
    return out


def merge_sheet(records: list[dict], sheet: dict) -> tuple[list[dict], dict]:
    """Merge a returned labelling sheet. Returns (records, report).

    Refuses silently-wrong merges rather than absorbing them: an unknown
    record_id, a duplicate annotator for the same item, or a score outside the
    criterion's scale is reported and NOT applied. A corpus that quietly accepted
    a malformed label would produce a ceiling nobody could audit.
    """
    annotator = str(sheet.get("annotator") or "").strip()
    by_id = {r.get("record_id"): r for r in records if r.get("record_id")}
    applied, skipped = 0, []
    if not annotator:
        return records, {"applied": 0, "skipped": ["sheet has no annotator id"]}

    out = to_multi_annotator(records)
    by_id = {r.get("record_id"): r for r in out if r.get("record_id")}
    for item in sheet.get("items") or []:
        rid = item.get("record_id")
        score = item.get("score")
        rec = by_id.get(rid)
        if rec is None:
            skipped.append(f"{rid}: no such record")
            continue
        if score is None:
            skipped.append(f"{rid}: annotator could not decide (kept unlabelled)")
            continue
        allowed = ((0.0, 1.0) if rec.get("scale") == "binary" else (0.0, 0.5, 1.0))
        if float(score) not in allowed:
            skipped.append(f"{rid}: score {score} outside {allowed}")
            continue
        labels = rec.setdefault("human_scores", [])
        if any(l.get("annotator") == annotator for l in labels):
            skipped.append(f"{rid}: {annotator} already labelled this item")
            continue
        labels.append({"annotator": annotator, "score": float(score)})
        applied += 1
    return out, {"applied": applied, "skipped": skipped,
                 "annotator": annotator,
                 "note": ("scores outside the scale, duplicate annotators and "
                          "unknown ids are reported and NOT applied — a corpus "
                          "that absorbs a malformed label produces a ceiling "
                          "nobody can audit")}


def readiness(records: list[dict]) -> dict:
    """Can a judge-vs-human study conclude anything from this corpus yet?

    Run before spending on a calibration run: a corpus that cannot discriminate
    makes the run worthless however it comes out.
    """
    from agenttic.scoring.annotators import corpus_health

    health = corpus_health([r for r in records if r.get("criterion_id")])
    blockers = sorted({b for h in health.values() for b in h["blockers"]})
    n_multi = sum(1 for r in records
                  if r.get("criterion_id") and len(labels_of(r)) >= 2)
    return {
        "criteria": sorted(health),
        "items": sum(1 for r in records if r.get("criterion_id")),
        "multi_annotated": n_multi,
        "ready": not blockers,
        "blockers": blockers,
        # The note must follow the BLOCKERS, not assume which one applies. It
        # said "with one label per item" unconditionally, which was false the
        # moment a second annotator landed and the remaining blocker was a
        # different one — a report that names the wrong cause sends the reader
        # to fix the wrong thing.
        "note": ("a study over this corpus can be read" if not blockers else
                 "; ".join(blockers)),
    }


def dumps_sheet(sheet: dict) -> str:
    return json.dumps(sheet, indent=2, ensure_ascii=False)
