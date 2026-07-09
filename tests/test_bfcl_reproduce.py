"""BFCL reproduction wedge (review #9): a genuinely reproducible number.

The published per-model leaderboard number needs a model API key (absent here),
so it stays honestly NOT reproduced. What IS reproduced offline, on real data, is
the GRADER: an oracle (ground-truth) prediction scores exactly 100%, and — proving
that's meaningful, not trivial — a wrong prediction scores wrong. These tests use
the vendored real BFCL sample only (no network).
"""

from __future__ import annotations

import pytest

from ascore.metrics.bfcl_reproduce import (
    ReproductionResult,
    _load_cases,
    model_predictions_available,
    oracle_predictions,
    reproduce_from_predictions,
    score_predictions,
    validate_scorer,
)


class TestGraderValidatedOnRealData:
    def test_oracle_scores_100_percent(self):
        # a correct BFCL AST grader must score the ground truth itself at 100%
        for split in ("simple", "multiple", "live_simple"):
            sc = validate_scorer(split)
            assert sc.n > 0
            assert sc.accuracy == 1.0, f"{split}: grader failed on ground truth"
            # exposes n + a Wilson interval for honest display
            low, high = sc.wilson
            assert 0.0 < low <= 1.0 and high == pytest.approx(1.0)

    def test_grader_is_not_trivially_100(self):
        # Feed a WRONG prediction — the grader must actually catch it, else the
        # oracle 100% would be meaningless.
        cases = _load_cases("simple")
        preds = oracle_predictions(cases)
        # corrupt one entry: wrong function name
        bid = next(iter(preds))
        preds[bid] = [{"name": "__definitely_wrong_tool__", "args": {}}]
        sc = score_predictions("simple", preds)
        assert sc.passes == sc.n - 1        # exactly the corrupted one fails
        assert sc.accuracy < 1.0

    def test_empty_predictions_score_zero(self):
        cases = _load_cases("simple")
        preds = {(c.expected or {}).get("bfcl_id", c.test_id): [] for c in cases}
        sc = score_predictions("simple", preds)
        assert sc.passes == 0


class TestReproductionComparison:
    def _oracle_result(self, published):
        cases = _load_cases("simple")
        return reproduce_from_predictions(
            "simple", "oracle", oracle_predictions(cases),
            published_accuracy=published, published_source="test")

    def test_reproduced_when_published_inside_interval(self):
        r = self._oracle_result(published=1.0)   # oracle acc 1.0, interval incl. 1.0
        assert isinstance(r, ReproductionResult)
        assert r.overlaps is True
        assert r.to_dict()["reproduced"] is True

    def test_not_reproduced_when_published_outside_interval(self):
        r = self._oracle_result(published=0.10)   # far below the interval
        assert r.overlaps is False
        assert r.to_dict()["reproduced"] is False

    def test_no_published_means_no_claim(self):
        r = self._oracle_result(published=None)
        assert r.overlaps is None
        assert r.to_dict()["reproduced"] is False


class TestHonestBlocker:
    def test_no_key_branch_cannot_produce_a_number(self, monkeypatch):
        # With no key, a per-model number cannot be produced (delenv to be robust
        # to test-ordering env leakage from other suites).
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert model_predictions_available() is False

    def test_key_detection_respects_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-xxx")
        assert model_predictions_available() is True
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert model_predictions_available() is False


class TestReproductionStatusSurface:
    def test_tool_calling_wedge_recorded_not_live(self, monkeypatch):
        # Corrected honest semantics: the published-number reproduction is a
        # RECORDED historical run, not a live re-measurement here (no key), so
        # reproduced (live) is False while recorded is True. The recorded figure
        # is still surfaced (not deleted).
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from ascore.metrics.reproduction import reproduction_report
        rep = reproduction_report()
        assert rep["any_reproduced"] is False           # nothing reproduced LIVE here
        assert rep["any_reproduced_recorded"] is True   # BFCL has a recorded run
        tc = {w["wedge"]: w for w in rep["wedges"]}["tool_calling"]
        assert tc["status"] == "reproduced_recorded"
        assert tc["reproduced"] is False and tc["recorded"] is True
        detail = tc["detail"]
        # the validated-grader evidence is still surfaced (oracle 100%)
        assert detail["scorer_validation_full_split"]["n"] == 400
        assert detail["scorer_validation_sample"]["accuracy"] == 1.0
        # the real reproduction: published falls inside our Wilson interval
        mr = detail["model_reproduction"]
        assert mr["model"] == "claude-sonnet-4-5-20250929"
        assert mr["published_within_interval"] is True
        assert mr["wilson_low"] <= mr["published_accuracy"] <= mr["wilson_high"]
        # both grader numbers reported for transparency (official reproduces)
        assert mr["reproduced_accuracy"] > mr["homegrown_grader_accuracy"]
