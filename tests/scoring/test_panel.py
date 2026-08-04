"""A panel of judges, and the property that makes it worth more than one judge.

The published result — several small judges beating one large one — rests on the
families DIFFERING, because the mechanism is decorrelated error plus the removal
of a judge's preference for its own family's output. Three judges from one lab
are close to one judge sampled three times.

So the tests that carry the weight are about the accounting, not the averaging.
Anything can call three models and take a median; the part that can be got wrong
silently is claiming the bias-reduction benefit while running a panel that does
not have it.
"""

from __future__ import annotations

import pytest

from agenttic.schema.scorecard import CriterionScore
from agenttic.scoring.panel import (JudgePanel, PanelVote, aggregate,
                                    make_panel, model_family,
                                    panel_independence)


class FakeJudge:
    """Stands in for LLMJudge — same surface: .model and .score_criterion."""

    def __init__(self, model, score=1.0, raises=None, cost=0.01):
        self.model, self._score, self._raises, self._cost = model, score, raises, cost

    def score_criterion(self, criterion, trace, tc) -> CriterionScore:
        if self._raises:
            raise self._raises
        return CriterionScore(criterion_id=criterion.criterion_id,
                              score=self._score, scorer="judge",
                              judge_rationale=f"{self.model} says so",
                              cost_usd=self._cost)


class Crit:
    criterion_id = "helpfulness"
    scorer = "judge"


def panel(*judges, agent="claude-sonnet-4-6", **kw):
    return JudgePanel(list(judges), agent_model=agent, **kw)


class TestFamilyIsTheUnitOfIndependence:
    @pytest.mark.parametrize("model,fam", [
        ("claude-opus-4-8", "anthropic"), ("gpt-4o", "openai"),
        ("o3-mini", "openai"), ("gemini-2.0-pro", "google"),
        ("llama-3.1-70b", "meta"), ("command-r-plus", "cohere"),
        ("mistral-large", "mistral"), ("deepseek-v3", "deepseek"),
    ])
    def test_known_families_are_identified(self, model, fam):
        assert model_family(model) == fam

    def test_a_provider_prefix_still_resolves(self):
        assert model_family("anthropic/claude-haiku-4-5") == "anthropic"

    def test_an_unrecognised_model_is_unknown_NOT_its_own_family(self):
        """Counting an unrecognised id as a distinct family would let a typo
        manufacture the diversity this module exists to verify."""
        assert model_family("totally-made-up-7b") == "unknown"
        assert model_family("") == "unknown"


class TestTheIndependenceAccounting:
    def test_a_single_family_panel_is_NOT_decorrelated(self):
        """The headline honesty case, and the shape of the shipped config."""
        r = panel_independence(["claude-haiku-4-5", "claude-opus-4-8"], "gpt-4o")
        assert r["decorrelated"] is False
        assert r["n_families"] == 1
        assert any("CANNOT cancel a bias they share" in b for b in r["blockers"])

    def test_a_cross_family_panel_clear_of_the_agent_is_decorrelated(self):
        r = panel_independence(["claude-haiku-4-5", "gpt-4o", "gemini-2.0-pro"],
                               "llama-3.1-70b")
        assert r["decorrelated"] is True and r["blockers"] == []
        assert r["n_families"] == 3

    def test_a_judge_sharing_the_agents_family_is_flagged_as_self_preference(self):
        r = panel_independence(["claude-opus-4-8", "gpt-4o"], "claude-sonnet-4-6")
        assert r["decorrelated"] is False
        assert any("self-preference" in b for b in r["blockers"])

    def test_a_judge_that_IS_the_agent_trips_hard_rule_4(self):
        r = panel_independence(["gpt-4o"], "gpt-4o")
        assert any("Hard Rule 4" in b for b in r["blockers"])

    def test_an_unknown_family_is_unverified_not_assumed_diverse(self):
        r = panel_independence(["claude-opus-4-8", "mystery-model"], "gpt-4o")
        assert r["decorrelated"] is False
        assert any("unverified rather than absent" in b for b in r["blockers"])

    def test_a_single_family_panel_is_still_reported_as_useful_not_refused(self):
        """Refusing it would be wrong — it does cut sampling variance. What it
        must not do is claim a benefit it does not have."""
        r = panel_independence(["claude-haiku-4-5", "claude-opus-4-8"], "gpt-4o")
        assert "reduce variance, not bias" in r["note"]


class TestAggregationObeysTheScale:
    def test_the_median_is_used_because_a_mean_is_not_on_the_scale(self):
        """mean(1, 1, 0) = 0.667, which CriterionScore's validator rejects
        (Hard Rule 3). This is a schema constraint, not a preference."""
        v = aggregate([PanelVote("a", "x", 1.0), PanelVote("b", "y", 1.0),
                       PanelVote("c", "z", 0.0)], criterion_id="c")
        assert v.score == 1.0
        CriterionScore(criterion_id="c", score=v.score, scorer="judge")

    def test_every_aggregate_is_a_value_some_judge_actually_returned(self):
        for scores in ([0.0, 0.5, 1.0], [0.5, 0.5, 1.0], [0.0, 0.0, 1.0]):
            v = aggregate([PanelVote(f"j{i}", "f", s)
                           for i, s in enumerate(scores)], criterion_id="c")
            assert v.score in scores

    def test_an_even_split_takes_the_LOWER_value(self):
        """Standing rule in this codebase: unproven is not passed, and a tie is
        by definition unproven."""
        v = aggregate([PanelVote("a", "x", 1.0), PanelVote("b", "y", 0.0)],
                      criterion_id="c")
        assert v.score == 0.0


class TestAPanelThatDegradesIsNotAPanel:
    def test_one_surviving_voter_yields_NO_verdict(self):
        """A single judge wearing a panel's credibility is the failure mode.
        None forces the caller to record a scoring error instead of a number."""
        v = aggregate([PanelVote("a", "x", 1.0),
                       PanelVote("b", "y", error="boom"),
                       PanelVote("c", "z", error="boom")], criterion_id="c")
        assert v.score is None
        assert "one judge with a panel's name" in v.note

    def test_a_failing_judge_is_recorded_as_data_not_swallowed(self):
        p = panel(FakeJudge("gpt-4o", 1.0),
                  FakeJudge("gemini-2.0-pro", raises=RuntimeError("429")),
                  FakeJudge("command-r", 1.0))
        score, verdict = p.score_criterion(Crit(), None, None)
        assert score.score == 1.0
        assert len(verdict.cast) == 2 and len(verdict.votes) == 3
        assert "RuntimeError: 429" in verdict.to_dict()["votes"][1]["error"]

    def test_the_scoring_error_path_returns_none_for_the_score(self):
        p = panel(FakeJudge("gpt-4o", raises=RuntimeError("boom")),
                  FakeJudge("gemini-2.0-pro", raises=RuntimeError("boom")))
        score, verdict = p.score_criterion(Crit(), None, None)
        assert score is None and verdict.score is None


class TestDisagreementIsReportedNotHidden:
    def test_unanimous_and_split_are_distinguishable(self):
        agreed = panel(FakeJudge("gpt-4o", 1.0), FakeJudge("gemini-2.0-pro", 1.0),
                       FakeJudge("command-r", 1.0))
        _s, v = agreed.score_criterion(Crit(), None, None)
        assert v.unanimous is True and v.dispersion == 0.0

    def test_a_full_scale_span_is_flagged_as_a_tie_break_not_a_measurement(self):
        p = panel(FakeJudge("gpt-4o", 1.0), FakeJudge("gemini-2.0-pro", 0.0),
                  FakeJudge("command-r", 1.0))
        _s, v = p.score_criterion(Crit(), None, None)
        assert v.contested is True and v.dispersion == 1.0
        assert "not agreement" in v.note

    def test_every_vote_reaches_the_rationale_with_its_family(self):
        p = panel(FakeJudge("gpt-4o", 1.0), FakeJudge("gemini-2.0-pro", 0.0),
                  FakeJudge("command-r", 0.0))
        score, _v = p.score_criterion(Crit(), None, None)
        assert "[openai] gpt-4o: 1.0" in score.judge_rationale
        assert "[google] gemini-2.0-pro: 0.0" in score.judge_rationale
        assert score.score == 0.0

    def test_the_cost_of_the_whole_panel_is_carried(self):
        p = panel(FakeJudge("gpt-4o", 1.0, cost=0.01),
                  FakeJudge("gemini-2.0-pro", 1.0, cost=0.02))
        score, v = p.score_criterion(Crit(), None, None)
        assert score.cost_usd == pytest.approx(0.03)
        assert v.to_dict()["cost_usd"] == pytest.approx(0.03)


class TestTheCertificateHashIsUntouched:
    def test_the_panel_detail_does_NOT_ride_on_criterionscore(self):
        """CriterionScore is embedded in RunScore -> Scorecard, and
        verify_manifest recomputes content_hash(scorecard). One new field here
        invalidates every certificate already issued.
        """
        allowed = set(CriterionScore.model_fields)
        p = panel(FakeJudge("gpt-4o", 1.0), FakeJudge("gemini-2.0-pro", 1.0))
        score, _v = p.score_criterion(Crit(), None, None)
        assert set(score.model_dump()) == allowed
        for leaked in ("votes", "panel", "dispersion", "families", "unanimous"):
            assert leaked not in allowed

    def test_the_verdict_is_returned_separately_so_it_can_be_stored_elsewhere(self):
        p = panel(FakeJudge("gpt-4o", 1.0), FakeJudge("gemini-2.0-pro", 1.0))
        result = p.score_criterion(Crit(), None, None)
        assert isinstance(result, tuple) and len(result) == 2


class TestItPromotesNothing:
    def test_a_panel_agreeing_with_itself_is_not_calibration(self):
        """Hard Rule 6. Three judges agreeing have demonstrated agreement with
        each other — calibration is against HUMANS, and that gate is elsewhere."""
        from agenttic.scoring.judge_calibration import \
            demonstrated_calibrated_judge

        p = panel(FakeJudge("gpt-4o", 1.0), FakeJudge("gemini-2.0-pro", 1.0),
                  FakeJudge("command-r", 1.0))
        score, _v = p.score_criterion(Crit(), None, None)
        assert score.calibrated is False
        assert demonstrated_calibrated_judge() == set()


class TestBuildingOneFromConfig:
    CFG = {"models": {"judge_light": "claude-haiku-4-5-20251001",
                      "judge_executor": "claude-sonnet-4-6",
                      "judge_strong": "claude-opus-4-8"}}

    def test_the_agents_own_model_is_never_on_its_panel(self):
        p = make_panel(self.CFG, "claude-sonnet-4-6", client=object())
        assert "claude-sonnet-4-6" not in p.models
        assert len(p.models) == 2

    def test_the_shipped_config_produces_a_panel_that_is_NOT_decorrelated(self):
        """Stated as a test so it cannot be quietly forgotten: every configured
        judge is Anthropic, and the agent under test usually is too. The panel
        machinery is real; the diversity is not there yet.
        """
        p = make_panel(self.CFG, "claude-sonnet-4-6", client=object())
        r = p.independence()
        assert r["decorrelated"] is False
        assert r["families"] == ["anthropic"]

    def test_an_explicit_panel_in_config_wins(self):
        cfg = dict(self.CFG, scoring={"judge_panel": ["gpt-4o", "gemini-2.0-pro",
                                                      "command-r-plus"]})
        p = make_panel(cfg, "claude-sonnet-4-6", client=object())
        assert p.independence()["decorrelated"] is True

    def test_nothing_left_to_panel_is_an_error_not_a_silent_single_judge(self):
        with pytest.raises(ValueError, match="Hard Rule 4"):
            make_panel({"models": {"judge_strong": "claude-opus-4-8"}},
                       "claude-opus-4-8", client=object())

    def test_an_empty_panel_is_refused(self):
        with pytest.raises(ValueError):
            JudgePanel([], agent_model="gpt-4o")
