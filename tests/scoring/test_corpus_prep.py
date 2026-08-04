"""Everything around the labelling, since the labelling itself must be human.

A model labelling data for a judge-vs-human study turns it into judge-vs-judge —
the independence failure the playbook opens with. The corpus is already
compromised on that axis (15 records, one label each, authored by the people who
wrote the judge prompt), so adding model labels would look like progress while
making it worse.

What IS automatable, and was missing: the multi-annotator shape, written
instructions so two people label the same question, a BLIND sheet, and a merge
that refuses malformed labels instead of absorbing them.
"""

from __future__ import annotations

import pytest

from agenttic.scoring.annotators import labels_of
from agenttic.scoring.corpus_prep import (INSTRUCTIONS, SHEET_FIELDS,
                                          labelling_sheet, merge_sheet,
                                          readiness, to_multi_annotator)


@pytest.fixture
def corpus():
    from agenttic.scoring.judge_calibration import load_judge_corpus
    return load_judge_corpus()


class TestTheSheetIsBlind:
    def test_it_never_shows_the_existing_label(self, corpus):
        """The load-bearing property. A second annotator shown the first label
        is CONFIRMING it, not producing an independent one — and a ceiling built
        from confirmations is an overestimate, which then makes the judge look
        worse than it is against a bar that is too high.
        """
        sheet = labelling_sheet(corpus, annotator="ben")
        for item in sheet["items"]:
            assert "human_score" not in item
            assert "human_scores" not in item

    def test_it_shows_what_is_needed_to_judge_the_criterion(self, corpus):
        sheet = labelling_sheet(corpus, annotator="ben")
        item = sheet["items"][0]
        for field in ("record_id", "criterion_id", "scale", "anchors",
                      "final_output"):
            assert field in item, field
        assert item["score"] is None

    def test_every_field_shown_is_on_the_declared_allowlist(self, corpus):
        """A sheet that grew a field by accident could leak the answer."""
        sheet = labelling_sheet(corpus, annotator="ben")
        allowed = set(SHEET_FIELDS) | {"score", "note"}
        for item in sheet["items"]:
            assert set(item) <= allowed, set(item) - allowed

    def test_the_instructions_travel_with_it(self, corpus):
        """Two people labelling different questions produce disagreement that
        measures the instructions, not the items."""
        sheet = labelling_sheet(corpus, annotator="ben")
        assert sheet["instructions"] == INSTRUCTIONS
        assert "Do not confer" in INSTRUCTIONS
        assert "cannot decide" in INSTRUCTIONS


class TestConversionIsLossless:
    def test_an_existing_label_becomes_the_first_annotator(self, corpus):
        out = to_multi_annotator(corpus)
        labelled = [r for r in out if r.get("criterion_id")]
        assert labelled
        for r in labelled:
            assert len(labels_of(r)) == 1
            assert r["human_score"] is not None      # old field kept working

    def test_it_is_idempotent(self, corpus):
        once = to_multi_annotator(corpus)
        assert to_multi_annotator(once) == once

    def test_a_row_with_no_criterion_passes_through_untouched(self):
        """The corpus FILE carries a leading `_comment` object; the loader
        strips it, but conversion must not corrupt one if it is ever passed a
        raw file — a lossless converter has to be lossless on what it ignores.
        """
        header = {"_comment": "corpus header"}
        out = to_multi_annotator([header, {"record_id": "r1",
                                           "criterion_id": "c",
                                           "human_score": 1.0}])
        assert out[0] == header
        assert out[1]["human_scores"] == [{"annotator": "a1", "score": 1.0}]


class TestTheMergeRefusesRatherThanAbsorbs:
    def test_a_good_sheet_applies(self, corpus):
        sheet = labelling_sheet(corpus, annotator="ben")
        for item in sheet["items"]:
            item["score"] = 1.0
        merged, report = merge_sheet(corpus, sheet)
        assert report["applied"] == len(sheet["items"])
        assert all(len(labels_of(r)) == 2
                   for r in merged if r.get("criterion_id"))

    def test_a_score_outside_the_scale_is_refused(self, corpus):
        sheet = labelling_sheet(corpus, annotator="ben")
        sheet["items"] = sheet["items"][:1]
        sheet["items"][0]["score"] = 0.7          # not in {0, 0.5, 1}
        _merged, report = merge_sheet(corpus, sheet)
        assert report["applied"] == 0
        assert any("outside" in s for s in report["skipped"])

    def test_a_binary_criterion_refuses_a_half(self, corpus):
        sheet = labelling_sheet(corpus, annotator="ben")
        binary = [i for i in sheet["items"] if i.get("scale") == "binary"][:1]
        assert binary, "the corpus should carry a binary criterion"
        binary[0]["score"] = 0.5
        sheet["items"] = binary
        _m, report = merge_sheet(corpus, sheet)
        assert report["applied"] == 0

    def test_the_same_annotator_cannot_label_twice(self, corpus):
        sheet = labelling_sheet(corpus, annotator="ben")
        for item in sheet["items"]:
            item["score"] = 1.0
        merged, _ = merge_sheet(corpus, sheet)
        _again, report = merge_sheet(merged, sheet)
        assert report["applied"] == 0
        assert any("already labelled" in s for s in report["skipped"])

    def test_cannot_decide_is_kept_unlabelled_not_guessed(self, corpus):
        """An honest "cannot decide" is data: two annotators splitting says the
        ITEM is ambiguous, which is a different problem from a wrong judge."""
        sheet = labelling_sheet(corpus, annotator="ben")
        sheet["items"] = sheet["items"][:1]
        sheet["items"][0]["score"] = None
        merged, report = merge_sheet(corpus, sheet)
        assert report["applied"] == 0
        assert any("could not decide" in s for s in report["skipped"])
        assert len(labels_of(merged[1] if "_comment" in merged[0] else merged[0])) == 1

    def test_an_unknown_record_is_refused(self, corpus):
        sheet = {"annotator": "ben",
                 "items": [{"record_id": "nope", "score": 1.0}]}
        _m, report = merge_sheet(corpus, sheet)
        assert report["applied"] == 0
        assert any("no such record" in s for s in report["skipped"])

    def test_a_sheet_with_no_annotator_is_refused(self, corpus):
        _m, report = merge_sheet(corpus, {"items": []})
        assert report["applied"] == 0


class TestReadiness:
    def test_the_shipped_corpus_is_not_ready_and_says_why(self, corpus):
        r = readiness(corpus)
        assert r["ready"] is False
        assert r["multi_annotated"] == 0
        assert any("two annotators" in b for b in r["blockers"])

    def test_the_note_follows_the_ACTUAL_blocker(self, corpus):
        """It said "with one label per item" unconditionally, which was false
        the moment a second annotator landed and a different blocker remained.
        A report naming the wrong cause sends the reader to fix the wrong thing.
        """
        sheet = labelling_sheet(corpus, annotator="ben")
        for i, item in enumerate(sheet["items"]):
            item["score"] = 1.0 if i % 3 else 0.0
        merged, _ = merge_sheet(corpus, sheet)
        after = readiness(merged)
        assert after["multi_annotated"] > 0
        assert "one label per item" not in after["note"]
        assert after["note"] == "; ".join(after["blockers"])

    def test_a_corpus_that_can_be_read_says_so(self):
        recs = [{"record_id": "r1", "criterion_id": "c", "scale": "binary",
                 "human_scores": [{"annotator": "a", "score": 1.0},
                                  {"annotator": "b", "score": 1.0}]},
                {"record_id": "r2", "criterion_id": "c", "scale": "binary",
                 "human_scores": [{"annotator": "a", "score": 0.0},
                                  {"annotator": "b", "score": 0.0}]}]
        r = readiness(recs)
        assert r["ready"] is True and r["blockers"] == []


class TestItPromotesNothing:
    def test_the_judge_gate_is_untouched(self):
        from agenttic.scoring.judge_calibration import demonstrated_calibrated_judge

        assert demonstrated_calibrated_judge() == set()
