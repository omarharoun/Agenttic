"""Several humans per item, and what follows from having them.

The point of this module is one distinction: a judge that disagrees with a
human is either wrong, or reading an item humans also disagree about. One label
per item cannot tell those apart, and they call for opposite fixes.

These tests pin the negative cases hardest — the ones where the honest answer is
"we cannot say" — because that is exactly where a calibration number gets
invented.
"""

from __future__ import annotations

import pytest

from agenttic.scoring.annotators import (Label, ceiling, consensus,
                                         corpus_health, labels_of,
                                         pairwise_agreement, scoped_agreement)


def rec(cid="helpfulness", rid="r1", **kw) -> dict:
    return {"record_id": rid, "criterion_id": cid, "scale": "three_point", **kw}


class TestReadingLabels:
    def test_the_legacy_single_label_still_loads(self):
        """The old corpus must keep working — a fix that breaks the thing it
        improves is not a fix."""
        assert labels_of(rec(human_score=0.5)) == [Label("a1", 0.5)]

    def test_multiple_annotators_are_read_with_their_names(self):
        got = labels_of(rec(human_scores=[{"annotator": "ana", "score": 1.0},
                                          {"annotator": "ben", "score": 0.5}]))
        assert [(x.annotator, x.score) for x in got] == [("ana", 1.0), ("ben", 0.5)]

    def test_a_bare_list_of_numbers_is_accepted(self):
        assert [x.score for x in labels_of(rec(human_scores=[1.0, 0.5]))] == [1.0, 0.5]

    def test_an_unlabelled_record_yields_nothing(self):
        assert labels_of(rec()) == []


class TestConsensus:
    def test_a_majority_decides(self):
        assert consensus([Label("a", 1.0), Label("b", 1.0), Label("c", 0.0)]) == 1.0

    def test_an_even_split_has_NO_consensus(self):
        """The honest answer, and the signal to get a third annotator.

        Returning a midpoint would invent 0.25 — a value a three-point scale
        does not contain and the judge can never produce, making the
        disagreement structurally impossible to resolve.
        """
        assert consensus([Label("a", 1.0), Label("b", 0.5)]) is None

    def test_agreeing_annotators_give_that_value(self):
        assert consensus([Label("a", 0.5), Label("b", 0.5)]) == 0.5

    def test_no_labels_is_none_not_zero(self):
        assert consensus([]) is None


class TestTheCeiling:
    def test_agreement_between_annotators_is_measured(self):
        recs = [rec(rid="r1", human_scores=[1.0, 1.0]),
                rec(rid="r2", human_scores=[1.0, 0.0])]
        c = ceiling(recs, "helpfulness")
        assert c["ceiling"] == 0.5           # one pair agrees, one does not
        assert c["n_multi_annotated"] == 2

    def test_single_annotator_items_give_NO_ceiling(self):
        """The state the shipped corpus is in. Reporting 1.0 here — 'the one
        annotator agrees with itself' — is the overclaim this prevents."""
        c = ceiling([rec(human_score=1.0)], "helpfulness")
        assert c["ceiling"] is None
        assert c["n_multi_annotated"] == 0
        assert "UNSCOPED" in c["note"]

    def test_items_with_no_consensus_are_named_for_a_third_label(self):
        c = ceiling([rec(rid="split", human_scores=[1.0, 0.0])], "helpfulness")
        assert c["ambiguous_items"] == ["split"]
        assert "third label" in c["note"]

    def test_one_annotator_alone_is_never_a_pair(self):
        assert pairwise_agreement([Label("a", 1.0)]) is None


class TestScopedAgreement:
    def test_a_figure_without_a_ceiling_is_labelled_unscoped(self):
        s = scoped_agreement(0.95, None)
        assert s["status"] == "unscoped"
        assert s["scoped"] is None
        assert "Add a second annotator" in s["note"]

    def test_agreement_is_reported_against_the_ceiling(self):
        s = scoped_agreement(0.62, 0.62)
        assert s["status"] == "scoped"
        assert s["scoped"] == 1.0
        assert "ceiling" in s["note"]

    def test_at_the_ceiling_is_not_failing(self):
        """0.62 against humans who agree 0.62 is the limit of the task, and must
        not read as a poor judge."""
        assert scoped_agreement(0.62, 0.62)["scoped"] == 1.0
        assert scoped_agreement(0.31, 0.62)["scoped"] == 0.5

    def test_above_the_ceiling_is_flagged_as_suspicious_not_excellent(self):
        s = scoped_agreement(0.9, 0.6)
        assert s["scoped"] == 1.5
        assert "reason to inspect the items" in s["note"]

    def test_no_judge_run_is_not_measured_rather_than_zero(self):
        s = scoped_agreement(None, 0.8)
        assert s["status"] == "not_measured" and s["scoped"] is None


class TestCorpusHealth:
    def test_a_corpus_where_every_label_is_identical_cannot_discriminate(self):
        """The defect in the shipped seed corpus, stated numerically.

        If every human label is 1.0, a judge that always answers 1.0 scores
        perfect agreement while having measured nothing.
        """
        recs = [rec(rid=f"r{i}", human_score=1.0) for i in range(5)]
        h = corpus_health(recs)["helpfulness"]
        assert h["discriminating"] is False
        assert any("always answers that value" in b for b in h["blockers"])

    def test_a_corpus_with_a_spread_of_labels_discriminates(self):
        recs = [rec(rid="r1", human_score=1.0), rec(rid="r2", human_score=0.0)]
        h = corpus_health(recs)["helpfulness"]
        assert h["discriminating"] is True

    def test_single_annotation_is_reported_as_a_blocker(self):
        recs = [rec(rid="r1", human_score=1.0), rec(rid="r2", human_score=0.0)]
        h = corpus_health(recs)["helpfulness"]
        assert any("unscoped" in b for b in h["blockers"])

    def test_a_healthy_corpus_has_no_blockers(self):
        recs = [rec(rid="r1", human_scores=[1.0, 1.0]),
                rec(rid="r2", human_scores=[0.0, 0.0])]
        h = corpus_health(recs)["helpfulness"]
        assert h["blockers"] == []
        assert h["discriminating"] and h["n_multi_annotated"] == 2


class TestTheShippedCorpus:
    def test_it_is_diagnosed_honestly_as_it_stands_today(self):
        """Run the diagnosis over the REAL corpus. It should report exactly the
        two blockers that make its recorded 1.0 agreement meaningless."""
        from agenttic.scoring.judge_calibration import load_judge_corpus

        health = corpus_health(load_judge_corpus())
        assert health, "the shipped corpus should be readable"
        for cid, h in health.items():
            assert h["n_multi_annotated"] == 0, (
                f"{cid} now has multi-annotated items — update this test and "
                "the calibration claim that depends on it")
            assert any("unscoped" in b for b in h["blockers"])

    def test_no_criterion_is_promoted_by_any_of_this(self):
        """Hard Rule 6 and the Step 14 gate are untouched."""
        from agenttic.scoring.judge_calibration import demonstrated_calibrated_judge

        assert demonstrated_calibrated_judge() == set()


class TestTheJudgeAsAClassifier:
    """Kappa + per-class recall, which raw agreement cannot substitute for.

    Both eval sources prescribe validating a judge "the same way you would
    validate any classifier (true positive rate, true negative rate, Cohen's
    kappa)", gated at kappa >= ~0.6 with a TNR high enough to actually catch
    failures. Their stated reason is our exact situation: on a class-imbalanced
    sample, raw agreement looks excellent for a judge that says PASS to
    everything.
    """

    def test_a_lenient_judge_scores_90_percent_agreement_and_fails_the_gate(self):
        """The headline case. Nine passes and one failure it missed: raw
        agreement 90%, and it has never once said FAIL."""
        from agenttic.scoring.annotators import classifier_report

        pairs = [(1.0, 1.0)] * 9 + [(1.0, 0.0)]
        r = classifier_report(pairs)
        assert r["kappa"] == 0.0
        assert r["tnr"] == 0.0          # caught none of the failures
        assert r["tpr"] == 1.0          # perfect on the class that dominates
        assert r["meets_gate"] is False

    def test_a_discriminating_judge_clears_it(self):
        from agenttic.scoring.annotators import classifier_report

        r = classifier_report([(1.0, 1.0)] * 5 + [(0.0, 0.0)] * 5)
        assert r["kappa"] == 1.0 and r["tnr"] == 1.0 and r["meets_gate"] is True

    def test_kappa_is_UNDEFINED_not_zero_when_a_rater_never_varies(self):
        """"Undefined" and "no better than chance" are different findings, and
        an all-PASS sample judged by an all-PASS judge produces the first."""
        from agenttic.scoring.annotators import cohens_kappa, classifier_report

        assert cohens_kappa([(1.0, 1.0)] * 5) is None
        r = classifier_report([(1.0, 1.0)] * 5)
        assert r["kappa"] is None
        assert any("undefined" in b for b in r["blockers"])

    def test_an_absent_class_is_unmeasurable_not_zero(self):
        """Calling a missing class 0.0 reports a CORPUS gap as a judge defect."""
        from agenttic.scoring.annotators import per_class_recall

        r = per_class_recall([(1.0, 1.0)] * 4)
        assert r["tnr"] is None and r["n_fail_items"] == 0
        assert "never been shown a failure" in r["note"]

    def test_a_sample_with_no_failure_blocks_the_gate(self):
        """Exactly the shipped corpus's shape for some criteria: you cannot
        certify a judge that has never been shown something to reject."""
        from agenttic.scoring.annotators import classifier_report

        r = classifier_report([(1.0, 1.0), (0.5, 1.0), (1.0, 1.0)])
        assert r["meets_gate"] is False
        assert any("no failing item" in b for b in r["blockers"])

    def test_three_point_half_counts_as_pass(self):
        from agenttic.scoring.annotators import confusion

        c = confusion([(0.5, 0.5)])
        assert c["tp"] == 1

    def test_it_is_reported_alongside_not_instead_of_agreement(self):
        """Replacing the calibration metric would move a published number.
        Adding one does not, and that distinction is the point."""
        from agenttic.scoring.annotators import classifier_report
        from agenttic.scoring import calibration

        assert hasattr(calibration, "krippendorff_alpha_interval")
        assert hasattr(calibration, "exact_match_rate")
        assert "alongside" in classifier_report([(1.0, 1.0)])["note"].lower()

    def test_the_confusion_matrix_totals_the_sample(self):
        from agenttic.scoring.annotators import confusion

        pairs = [(1.0, 1.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)]
        c = confusion(pairs)
        assert (c["tp"], c["fp"], c["fn"], c["tn"]) == (1, 1, 1, 1)
        assert c["tp"] + c["fp"] + c["fn"] + c["tn"] == c["n"] == 4
