"""The reference model's verdict reaches the sign-off — report-only, for now.

`stimulus/oracle.py:10` says it plainly: *"the abstract point plus the policy IS
the reference model."* And the comparison already existed — `oracle_failures()`
and `state_failures()` in `scenario/runner.py` check `forbidden_tools`,
`must_escalate` and `goal_state_delta` against what the run actually did, and
`harness_executor.execute` gates `ExecutionResult.passed` on them.

It just never reached the scoreboard. `ops.cdv_op` builds the Scorecard from the
scoring engine's `RunScore`s and drops the oracle findings, so the reference
model's verdict was absent from every scorecard, tier and certificate.

Two things this must not do, and both are pinned here:

* **It must not break a single issued certificate.** `certification/attest.py`
  recomputes `content_hash(scorecard)` at verify time, so the leg lives on
  `VerificationSignoff` — never recomputed there — and `Scorecard` is untouched.
* **It must not retroactively fail a stored sign-off.** A gate that gains a
  condition has to say which gate it was issued under, or every past sign-off is
  judged by a rule that did not exist when it was made.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from agenttic.schema.signoff import (AssertionLeg, CoverageLeg, ScoreboardLeg,
                                     VerificationSignoff, build_signoff,
                                     scoreboard_leg)


def clean_signoff(**kw) -> VerificationSignoff:
    """A sign-off that signs: closure met, no violations, nothing crashed."""
    s = VerificationSignoff(signoff_id="s", agent_id="a", **kw)
    s.coverage = CoverageLeg(status="populated", closed=True, trace_closure=0.96)
    s.assertions = AssertionLeg(status="populated", total=3, violations=0,
                                evaluations=3, evaluations_submitted=3)
    return s


class TestItIsReportOnlyUnderV1:
    def test_a_violating_scoreboard_still_signs_at_gate_version_1(self):
        """The statement that stays true forever, so v2 can be ADDED rather
        than edited into this test later."""
        s = clean_signoff()
        assert s.gate_version == 1
        s.scoreboard = ScoreboardLeg(status="populated", compared=4, violations=2)
        assert s.signs_off is True

    def test_and_says_nothing_about_it_in_the_refusals(self):
        """Naming a blocker that does not block sends the reader to fix the
        wrong thing."""
        s = clean_signoff()
        s.scoreboard = ScoreboardLeg(status="populated", violations=2)
        assert not [r for r in s.refusal_reasons() if "obligation" in r]

    def test_a_stored_signoff_without_the_field_still_signs(self):
        """Every sign-off issued before this leg existed must re-validate."""
        s = clean_signoff()
        assert s.scoreboard.status == "not_run"
        assert s.signs_off is True

    def test_the_leg_is_not_in_LEGS_yet(self):
        """`missing_legs`/`complete` drive report text for every past sign-off;
        adding to LEGS is part of the v2 flip, not of shipping the leg."""
        assert "scoreboard" not in VerificationSignoff.LEGS


class TestItBlocksUnderV2:
    def test_violations_block(self):
        s = clean_signoff(gate_version=2)
        s.scoreboard = ScoreboardLeg(status="populated", compared=4, violations=1)
        assert s.signs_off is False

    def test_a_clean_comparison_signs(self):
        s = clean_signoff(gate_version=2)
        s.scoreboard = ScoreboardLeg(status="populated", compared=4, violations=0)
        assert s.signs_off is True

    def test_a_comparison_that_could_not_RUN_blocks(self):
        """The vacuity rule turned on the comparator: `violations == 0` must not
        be satisfiable by crashing. Same reason `AssertionLeg` blocks on
        `evaluation_failures`."""
        s = clean_signoff(gate_version=2)
        s.scoreboard = ScoreboardLeg(status="populated", compared=4,
                                     violations=0, comparison_failures=1)
        assert s.signs_off is False

    def test_a_leg_that_never_ran_blocks_rather_than_passing(self):
        s = clean_signoff(gate_version=2)
        assert s.scoreboard.status == "not_run"
        assert s.signs_off is False

    def test_the_refusal_names_the_obligation(self):
        s = clean_signoff(gate_version=2)
        s.scoreboard = ScoreboardLeg(
            status="populated", compared=2, violations=1,
            violated_obligations=["oracle.forbidden_tools"])
        why = " ".join(s.refusal_reasons())
        assert "oracle.forbidden_tools" in why

    @pytest.mark.parametrize("field", ["violations", "comparison_failures"])
    def test_refusals_mirror_signs_off_condition_for_condition(self, field):
        s = clean_signoff(gate_version=2)
        s.scoreboard = ScoreboardLeg(status="populated", compared=1, **{field: 1})
        assert s.signs_off is False
        assert s.refusal_reasons(), f"{field} blocks but is never explained"


class TestTheRollup:
    def test_it_carries_the_findings_that_already_existed(self):
        """Nothing new is derived — `oracle_failures()`/`state_failures()`
        return these and `harness_executor` already gates on them."""
        leg = scoreboard_leg(
            [[NS(signature="oracle.forbidden_tools")],
             [NS(signature="oracle.goal_state")], []], compared=3)
        assert leg.status == "populated"
        assert leg.compared == 3 and leg.violations == 2
        assert set(leg.violated_obligations) == {"oracle.forbidden_tools",
                                                 "oracle.goal_state"}

    def test_no_expectation_is_NOT_MEASURED_and_never_a_pass(self):
        """A stored suite runs no environment, so the state half of correctness
        cannot be observed. Saying so differs from saying the agent behaved."""
        leg = scoreboard_leg([], compared=0, not_measured=12)
        assert leg.not_measured == 12 and leg.violations == 0
        assert "NOT counted as passing" in leg.scope_note

    def test_nothing_compared_and_nothing_measured_is_not_run(self):
        assert scoreboard_leg([], compared=0).status == "not_run"

    def test_build_signoff_leaves_it_not_run_when_absent(self):
        s = build_signoff(signoff_id="s", agent_id="a")
        assert s.scoreboard.status == "not_run"

    def test_build_signoff_carries_it_when_supplied(self):
        leg = scoreboard_leg([[]], compared=1)
        s = build_signoff(signoff_id="s", agent_id="a", scoreboard=leg)
        assert s.scoreboard.status == "populated" and s.scoreboard.compared == 1


class TestCertificatesAreUntouched:
    def test_the_scorecard_schema_gained_nothing(self):
        """`verify_manifest` recomputes `content_hash(scorecard)`, so a field
        added to Scorecard — or to RunScore/CriterionScore, which it embeds —
        would invalidate every certificate ever issued."""
        from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard

        assert "scoreboard" not in Scorecard.model_fields
        assert "scoreboard" not in RunScore.model_fields
        assert "scoreboard" not in CriterionScore.model_fields

    def test_the_signoff_hash_covers_the_new_leg(self):
        """The sign-off IS hashed at signing time, so the leg must be inside
        that hash — evidence outside the hash is evidence nobody signed."""
        a = clean_signoff()
        b = clean_signoff()
        b.scoreboard = ScoreboardLeg(status="populated", compared=1, violations=1)
        assert a.content_sha256() != b.content_sha256()
