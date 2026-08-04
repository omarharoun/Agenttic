"""The reference model's verdict, carried to the scoreboard.

The comparison always existed — `harness_executor` gates every run's `passed` on
its oracle findings — and it was dropped on the floor between the executor and
the scorecard, so the leg sat at `not_run` in production while the evidence for
it was computed on every scenario run.

The subtlety this pins is that `oracle_failures` returns `[]` for three
different facts: a missing expectation, an expectation that could not have
failed, and a genuinely clean run. Rolling all three up as "compared, zero
violations" would manufacture a clean scoreboard out of nothing tested — which
is the M40 vacuity rule (unexercised is not a pass) failing at the one place
that claims to check correctness.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agenttic.scenario.runner import (CHECKED_OBLIGATIONS,
                                      expectation_is_decidable,
                                      scoreboard_from_runs)
from agenttic.schema.trace import Trace
from agenttic.stimulus.oracle import Expectation


class Sig:
    def __init__(self, signature):
        self.signature = signature


@dataclass
class FakeScenario:
    expectation: object | None = None


@dataclass
class FakeRun:
    scenario: FakeScenario
    trace: Trace
    oracle_findings: list = field(default_factory=list)


def trace(output="did the thing") -> Trace:
    # black_box because it is the visibility that legally carries no spans, and
    # the bucketing here reads the expectation and the non-result marker, never
    # the spans — oracle_failures has already run by this point.
    return Trace(trace_id="t1", test_case_id="c1", agent_id="a",
                 agent_config_hash="cfg", visibility="black_box",
                 final_output=output, spans=[])


def run(*, expectation=None, findings=(), output="did the thing") -> FakeRun:
    return FakeRun(FakeScenario(expectation), trace(output), list(findings))


FORBIDS = Expectation(forbidden_tools=["wire_transfer"])


class TestDecidability:
    def test_an_expectation_with_a_checkable_obligation_is_decidable(self):
        assert expectation_is_decidable(FORBIDS) is True
        assert expectation_is_decidable(Expectation(must_escalate=True)) is True
        assert expectation_is_decidable(
            Expectation(goal_state_delta={"balance": 0})) is True

    def test_no_expectation_is_not_decidable(self):
        assert expectation_is_decidable(None) is False

    def test_an_expectation_that_declares_NOTHING_is_not_decidable(self):
        """It produces [] for every run, which is byte-identical to 'compared
        and clean'. Counting it as compared invents a passing comparison."""
        assert expectation_is_decidable(Expectation()) is False

    def test_must_convey_alone_does_not_make_it_decidable(self):
        """Nothing checks must_convey (stated in the runner docstring), so
        counting it would make an expectation look decidable because of a field
        no comparison reads."""
        assert "must_convey" not in CHECKED_OBLIGATIONS
        assert expectation_is_decidable(
            Expectation(must_convey=["the refund policy"])) is False

    def test_should_grant_alone_does_not_either(self):
        assert expectation_is_decidable(Expectation(should_grant=True)) is False


class TestTheThreeBucketsStayApart:
    def test_a_clean_compared_run_populates_the_leg(self):
        leg = scoreboard_from_runs([run(expectation=FORBIDS)])
        assert leg.status == "populated"
        assert leg.compared == 1 and leg.violations == 0
        assert leg.not_measured == 0 and leg.comparison_failures == 0

    def test_a_violation_is_carried_with_its_signature(self):
        leg = scoreboard_from_runs([
            run(expectation=FORBIDS, findings=[Sig("oracle.forbidden_tools")])])
        assert leg.violations == 1
        assert leg.violated_obligations == ["oracle.forbidden_tools"]

    def test_the_real_producer_renders_as_a_key_not_a_dataclass_repr(self):
        """FailureSignature has no `.signature`, so the fallback was writing its
        Python repr into the SIGNED sign-off — in the one field a reader scans
        to see what was violated."""
        from agenttic.verification.cdv import FailureSignature

        f = FailureSignature("oracle.must_escalate", "never_escalated", "chain")
        leg = scoreboard_from_runs([run(expectation=FORBIDS, findings=[f])])
        assert leg.violated_obligations == [
            "oracle.must_escalate|never_escalated|chain"]
        assert "FailureSignature(" not in leg.violated_obligations[0]

    def test_an_undecidable_expectation_is_not_measured_NOT_a_pass(self):
        leg = scoreboard_from_runs([run(expectation=Expectation())])
        assert leg.compared == 0 and leg.not_measured == 1
        assert leg.violations == 0
        assert "NOT counted as passing" in leg.scope_note

    def test_a_run_that_never_reached_the_agent_is_a_comparison_FAILURE(self):
        """The oracle inspected a transport error and found no violation in it.
        Counting that as clean is the vacuity failure the leg exists to prevent.
        """
        leg = scoreboard_from_runs([run(expectation=FORBIDS,
                                        output="HARNESS_FAILURE:timeout")])
        assert leg.comparison_failures == 1
        assert leg.compared == 0 and leg.violations == 0
        assert "never reached the agent" in leg.scope_note

    def test_a_mixed_batch_keeps_every_bucket_visible(self):
        leg = scoreboard_from_runs([
            run(expectation=FORBIDS),
            run(expectation=FORBIDS, findings=[Sig("oracle.must_escalate")]),
            run(expectation=Expectation()),
            run(expectation=None),
            run(expectation=FORBIDS, output="BLACKBOX_FAILURE:ConnectionError"),
        ])
        assert (leg.compared, leg.not_measured, leg.comparison_failures) == (2, 2, 1)
        assert leg.violations == 1

    def test_an_empty_batch_stays_not_run(self):
        leg = scoreboard_from_runs([])
        assert leg.status == "not_run" and leg.compared == 0


class TestThePlumbing:
    def test_verify_op_accepts_a_scoreboard_and_puts_it_on_the_signoff(self):
        from agenttic import ops

        leg = scoreboard_from_runs([run(expectation=FORBIDS)])
        _results, summary = ops.verify_op([trace()], scoreboard=leg)
        assert summary["signoff"]["scoreboard"]["status"] == "populated"
        assert summary["signoff"]["scoreboard"]["compared"] == 1

    def test_without_one_the_leg_stays_not_run(self):
        """A stored suite runs no environment, so there is no state to compare —
        `not_run` is the honest reading, not an omission to paper over."""
        from agenttic import ops

        _results, summary = ops.verify_op([trace()])
        assert summary["signoff"]["scoreboard"]["status"] == "not_run"

    def test_aggregate_op_passes_it_through(self):
        import inspect

        from agenttic import ops

        assert "scoreboard" in inspect.signature(ops.aggregate_op).parameters
        assert "scoreboard=scoreboard" in inspect.getsource(ops.aggregate_op)

    def test_a_REAL_cdv_run_reaches_the_scorecard_with_the_leg_populated(
            self, tmp_path):
        """End to end on the actual production path — scripted agent, no key, no
        network. Source inspection would pass while the value never arrived; the
        defect being fixed is precisely that the wiring looked done.
        """
        from tests.test_ops import _cdv_cfg, _cdv_reg, _run_cdv

        out = _run_cdv(_cdv_reg(tmp_path), _cdv_cfg(tmp_path),
                       scenarios=4, rounds=1)
        leg = out.scorecard.signoff["scoreboard"]
        assert leg["status"] == "populated", leg
        assert leg["compared"] + leg["not_measured"] == len(out.runs)
        assert leg["scope_note"]

    def test_that_leg_was_not_run_before_this_change(self, tmp_path):
        """The state in production: every scenario run computed the comparison
        and the scorecard carried none of it."""
        from agenttic import ops
        from tests.test_ops import _cdv_cfg, _cdv_reg, _run_cdv

        out = _run_cdv(_cdv_reg(tmp_path), _cdv_cfg(tmp_path),
                       scenarios=4, rounds=1)
        _r, summary = ops.verify_op([r.trace for r in out.runs])   # no scoreboard
        assert summary["signoff"]["scoreboard"]["status"] == "not_run"


class TestTheSigningGateIsUnmoved:
    """Hard Rule: never change the promotion gate. Populating a leg must not
    flip a verdict, and `gate_version` is what guarantees it."""

    def _signoff(self, **kw):
        from agenttic.schema.signoff import VerificationSignoff
        return VerificationSignoff(signoff_id="s", agent_id="a", **kw)

    def test_v1_treats_the_scoreboard_as_report_only(self):
        s = self._signoff()
        assert s.gate_version == 1
        before = s.signs_off
        s.scoreboard = scoreboard_from_runs([
            run(expectation=FORBIDS, findings=[Sig("oracle.forbidden_tools")])])
        assert s.signs_off == before

    def test_a_violation_under_v1_adds_no_refusal_reason(self):
        s = self._signoff()
        s.scoreboard = scoreboard_from_runs([
            run(expectation=FORBIDS, findings=[Sig("oracle.forbidden_tools")])])
        assert not any("obligation" in r for r in s.refusal_reasons())

    def test_under_v2_it_would_gate_and_says_so(self):
        """Not shipped as the default — pinned so the change stays deliberate."""
        s = self._signoff(gate_version=2)
        s.scoreboard = scoreboard_from_runs([
            run(expectation=FORBIDS, findings=[Sig("oracle.forbidden_tools")])])
        assert s.signs_off is False
        assert any("obligation" in r for r in s.refusal_reasons())

    def test_under_v2_a_comparison_failure_blocks_on_the_denominator(self):
        """One dead run among live ones: `violations == 0` is then true over a
        denominator smaller than the batch, which is not the same claim."""
        s = self._signoff(gate_version=2)
        s.scoreboard = scoreboard_from_runs([
            run(expectation=FORBIDS),
            run(expectation=FORBIDS, output="HARNESS_FAILURE:timeout")])
        assert s.scoreboard.status == "populated"
        assert s.signs_off is False
        assert any("reduced denominator" in r for r in s.refusal_reasons())

    def test_a_batch_where_NOTHING_was_compared_stays_not_run(self):
        """Comparison failures alone must not populate the leg. If every run
        died in transport, the reference model compared nothing — and "unscoped"
        is the honest refusal, not "clean with a caveat"."""
        s = self._signoff(gate_version=2)
        s.scoreboard = scoreboard_from_runs([
            run(expectation=FORBIDS, output="HARNESS_FAILURE:timeout")])
        assert s.scoreboard.status == "not_run"
        assert s.signs_off is False
        assert any("never compared" in r for r in s.refusal_reasons())


class TestTheCertificateHashIsUntouched:
    def test_the_leg_lives_on_the_signoff_and_NOT_on_the_scorecard(self):
        """verify_manifest recomputes content_hash(scorecard); a field added
        there invalidates every certificate ever issued."""
        from agenttic.schema.scorecard import Scorecard

        assert "scoreboard" not in Scorecard.model_fields

    def test_the_signoff_is_not_recomputed_at_verify_time(self):
        import inspect

        from agenttic.certification import attest

        src = inspect.getsource(attest)
        assert "content_hash(scorecard)" in src
        assert "content_hash(signoff)" not in src
