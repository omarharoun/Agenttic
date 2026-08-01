"""P5 — wiring the CDV loop to the real harness, and the stimulus side to coverage.

Two dead surfaces are on trial.

**The executor seam.** ``run_until_closure`` has taken an injected ``execute``
since SPEC-13 and has never had a production caller: every call site in the repo
is a test injecting a stand-in that ignores the scenario. So the loop that
"closes coverage instead of counting passes" had never been pointed at an agent,
and neither had the bug-discovery curve, the frozen regressions or replay.

**The stimulus side.** ``ops.verify_op`` built its coverage samples as
``Sample(trace=t)`` — no scenario, no ``requested``. ``requested`` is the only
source of ``stimulus_hits`` (``coverage/collect.py:580``), so on every run the
product has ever performed ``stimulus_closure`` was 0.0 and ``divergence()`` was
``[]``. *What we asked to test* versus *what the run exhibited* — the two-number
story the coverage module was built around — has never once been visible.

The anti-overclaim tests matter as much as the enabling ones:
``VerificationSignoff.signs_off`` binds on coverage + assertions + formal ONLY.
Convergence and envelope are SCOPE. This phase makes the report true; it does not
make the gate stricter, and a later phase must not quietly reinterpret it as
having done so.

Offline throughout, under the ``no_network`` fixture.
"""

from __future__ import annotations

import json

import pytest

from agenttic import ops
from agenttic.coverage.collect import Sample, collect
from agenttic.coverage.models.baseline import baseline_model
from agenttic.registry.sqlite_store import NotFoundError, Registry
from agenttic.scenario.runner import (
    ScenarioAgent, ScenarioOutcome, ScriptedSupportClient, scenario_runner)
from agenttic.schema.rubric import Rubric
from agenttic.schema.scorecard import RunScore
from agenttic.schema.signoff import build_signoff
from agenttic.schema.trace import Trace
from agenttic.stimulus.space import Dimension, ScenarioSpace
from agenttic.stimulus.spaces.conversational_transactional import seed_space
from agenttic.verification.cdv import Budget, CDVResult
from tests.verification.conftest import span, trace

pytestmark = pytest.mark.usefixtures("no_network")

CFG = {
    "models": {"agent_default": "scripted-support", "judge_strong": "judge-model"},
    "harness": {"timeout_seconds": 10, "max_parallel": 4, "transport_retries": 1,
                "max_steps": 8},
    "scoring": {"max_parallel": 4},
    "coverage": {"closure_target": 0.95},
    "cdv": {"batch_size": 10},
}

RUBRIC = Rubric(rubric_id="r-cdv", version=1, criteria=[
    {"criterion_id": "step_budget", "description": "within the step budget",
     "scorer": "code", "scale": "binary", "check_ref": "steps_under_limit"},
    {"criterion_id": "cost_budget", "description": "within the cost budget",
     "scorer": "code", "scale": "binary", "check_ref": "cost_under_limit"},
])


@pytest.fixture
def reg(tmp_path) -> Registry:
    r = Registry(str(tmp_path / "cdv.db"))
    r.save_rubric(RUBRIC)
    return r


@pytest.fixture
def cfg(tmp_path) -> dict:
    return {**CFG, "paths": {"review_dir": str(tmp_path / "review")}}


def _agent(agent_id: str = "cdv-dut") -> ScenarioAgent:
    return ScenarioAgent(model="scripted-support", client=ScriptedSupportClient(),
                         agent_id=agent_id)


def _tool_trace(name: str = "lookup_order") -> Trace:
    return trace(span("tool_call", name, i=0, output={"status": "delivered"}),
                 span("final_output", "final_output", i=1,
                      output={"text": "here you go"}))


def _toy_space() -> ScenarioSpace:
    """One dimension, three read-only intents.

    Small on purpose: every point it can draw derives an expectation with no
    forbidden call the stub can make and no escalation obligation, so a test
    about the DETECTOR is not quietly also a test about the oracle.
    """
    return ScenarioSpace(space_id="space-toy", version=1,
                         dimensions=(Dimension("intent",
                                               ("status", "complaint", "other")),))


def _stub_runner(cost: float = 0.01):
    """A ScenarioRunner double: a real Trace, no world, no agent."""
    def run(scenario, *, adapter, store):
        t = _tool_trace().model_copy(update={"trace_id": scenario.scenario_id,
                                             "total_cost_usd": cost,
                                             "total_steps": 2})
        return ScenarioOutcome(trace=t, state_diff={}, interactions=[])
    return run


# --------------------------------------------------------------------------- #
# 1. the stimulus side reaches coverage
# --------------------------------------------------------------------------- #


class TestTheStimulusSideReachesTheReport:
    def test_verify_op_records_what_was_requested(self):
        """Before this, ``stimulus_closure`` was 0.0 on every run the product
        performed — not a finding, a missing input."""
        samples = [Sample(trace=_tool_trace(), scenario=None,
                          requested={"tool_condition": "timeout"})]
        _, summary = ops.verify_op([s.trace for s in samples], cfg=CFG,
                                   samples=samples)
        assert summary["stimulus_closure"] > 0.0

    def test_divergence_names_the_bin_we_asked_for_and_never_got(self):
        """The anti-coverage-theatre invariant. A scenario that requested a
        timeout and got a clean run is a DIVERGENCE, not coverage."""
        samples = [Sample(trace=_tool_trace(), scenario=None,
                          requested={"tool_condition": "timeout"})]
        _, summary = ops.verify_op([s.trace for s in samples], cfg=CFG,
                                   samples=samples)
        assert summary["stimulus_vs_trace_divergence"] == [
            {"coverpoint_id": "tool_condition", "bin_id": "timeout",
             "requested": 1, "exhibited": 0}]

    def test_the_old_call_shape_is_unchanged(self):
        """Every existing caller (``metrics/runner.py``, the server run-node, the
        red-team paths) passes traces and nothing else. The two new keys are
        additive and read 0.0 / [] — which is the honest reading for a caller
        that requested nothing, and must never be rendered as a finding."""
        t = _tool_trace()
        _, summary = ops.verify_op([t], cfg=CFG)
        assert summary["stimulus_closure"] == 0.0
        assert summary["stimulus_vs_trace_divergence"] == []
        for key in ("model_ref", "trace_closure", "closure_target", "closed",
                    "samples", "non_results", "per_coverpoint", "holes",
                    "signoff"):
            assert key in summary

    def test_the_stimulus_ceiling_under_this_pairing_is_two_fifths(self):
        """Requested is capped by what the SPACE can ask for, and the space is
        not the coverage model.

        ``seed_space()`` v2 turns five knobs, two of which are coverpoints of
        ``baseline_model()`` v3 (``tool_condition``, ``data_condition``). The
        other three scored coverpoints — ``trajectory``, ``agent_steps``,
        ``action_risk`` — are run OUTPUTS and cannot be requested at all, so the
        arithmetic maximum is 2/5 = 0.4 and a later phase must not read it as a
        shortfall.

        The P5 spec states 0.6 here. That figure predates baseline v3 (which
        split `agent_steps` out of `session_shape`) and space v2 (which deleted
        the `session_shape` dimension); it is recomputed rather than restated,
        because a spec figure copied forward is exactly the kind of number this
        product exists to refuse.
        """
        space = seed_space()
        samples = [Sample(trace=_tool_trace(), scenario=None,
                          requested={d.dim_id: v})
                   for d in space.dimensions for v in d.values]
        report = collect(baseline_model(cfg=CFG), samples)
        assert report.stimulus_closure == pytest.approx(0.4)
        for cp in ("trajectory", "agent_steps", "action_risk"):
            assert report.coverpoints[cp].stimulus_closure == 0.0


# --------------------------------------------------------------------------- #
# 2. the loop, wired to the real harness
# --------------------------------------------------------------------------- #


class TestTheLoopRunsAgainstARealAgent:
    def test_cdv_op_wires_the_loop_to_the_harness(self, reg, cfg):
        out = ops.cdv_op(cfg, reg, _agent(), space=seed_space(), rubric=RUBRIC,
                         run_scenario=scenario_runner(), seed=3,
                         budget=Budget(max_scenarios=20, max_dollars=5.0,
                                       max_rounds=2))
        assert out.cdv.scenarios_run == 20
        assert len(out.runs) == 20
        # criterion 4: `requested` IS the point the solver drew
        for r in out.runs:
            assert r.sample().requested == dict(r.scenario.point)
            assert r.sample().scenario["scenario_id"] == r.scenario.scenario_id
        # criterion 5: two computations of the same coverage agree
        assert out.scorecard.coverage["trace_closure"] == pytest.approx(
            round(out.cdv.report.trace_closure, 4))
        assert out.scorecard.coverage["stimulus_closure"] > 0.0

    def test_convergence_and_envelope_stop_being_not_run(self, reg, cfg):
        """Every sign-off the product has ever issued reads "4 · CONVERGENCE not
        run / 6 · ENVELOPE not run", because no production caller passed a
        cdv_result."""
        out = ops.cdv_op(cfg, reg, _agent(), space=seed_space(), rubric=RUBRIC,
                         run_scenario=scenario_runner(), seed=3,
                         budget=Budget(max_scenarios=10, max_dollars=5.0,
                                       max_rounds=1))
        signoff = out.scorecard.signoff
        assert signoff["convergence"]["status"] == "populated"
        assert signoff["convergence"]["scenarios_run"] == out.cdv.scenarios_run > 0
        assert signoff["envelope"]["status"] == "populated"
        assert signoff["envelope"]["closure_per_dollar"] > 0

    def test_closure_rises_as_the_loop_runs(self, reg, cfg):
        """The loop's whole claim. Closure is measured on what runs EXHIBITED,
        so this cannot be satisfied by asking for more."""
        out = ops.cdv_op(cfg, reg, _agent(), space=seed_space(), rubric=RUBRIC,
                         run_scenario=scenario_runner(), seed=5,
                         budget=Budget(max_scenarios=30, max_dollars=5.0,
                                       max_rounds=3))
        closures = [r.closure for r in out.cdv.rounds]
        assert len(closures) == 3
        assert closures == sorted(closures), closures     # monotone, never a dip
        assert closures[-1] > closures[0]

    def test_biasing_aims_at_unhit_bins_rather_than_uniformly(self, reg, cfg):
        """Coverage-DIRECTED, not coverage-measured. The control arm is the same
        seeds with ``bias=False``: if the aiming does nothing, the two stimulus
        distributions are identical.

        The claim under test is that the solver AIMS — deliberately, and not that
        aiming improves closure. That separation has earned its keep twice.

        It used to be a producer gap: the highest-ranked holes were the five
        ``tool_condition`` fault bins and nothing could inject a fault, so the
        loop aimed at a corner no producer could reach. The injector closed that
        (0.5846 in every arm → 0.66–0.74 on seeds 5/11/23). Then it was a ranking
        gap: ``holes()`` gives every cross the same structural rank and ties break
        alphabetically, so the top ten holes reduced to ``tool_condition=all_ok``
        and the biased arm finished 2–5 points BELOW unbiased on every seed.
        ``holes_to_targets`` now demotes already-exhibited components, collapses
        duplicate target sets, and advances through the list across rounds; over
        21 seeds that took biasing from 3/21 wins (mean 0.6783) to 10/21 (mean
        0.7342) against an unbiased mean of 0.7279 — see ``ops.cdv_op`` for the
        table.

        This test STILL does not assert an improvement, and should not start. A
        +0.006 mean that loses on 9 of 21 seeds is not a property a test can hold
        a build to without becoming a flake, and the thing worth protecting is
        the mechanism: the biased arm's stimulus distribution is pulled toward
        the targets. If a future change breaks the aiming, that shows up here
        whatever the closure numbers happen to do.
        """
        common = dict(space=seed_space(), rubric=RUBRIC,
                      run_scenario=scenario_runner(), seed=5,
                      budget=Budget(max_scenarios=30, max_dollars=5.0,
                                    max_rounds=3))
        biased = ops.cdv_op(cfg, reg, _agent("biased"), bias=True, **common)
        control = ops.cdv_op(cfg, reg, _agent("control"), bias=False, **common)

        def requested(res, dim):
            out: dict[str, int] = {}
            for scn in res.scenarios:
                out[scn.point[dim]] = out.get(scn.point[dim], 0) + 1
            return out

        b = requested(biased.cdv, "tool_condition")
        c = requested(control.cdv, "tool_condition")
        assert b != c, "biasing changed nothing about what was asked for"
        # every biased round names the bins it aimed at, and they are bins the
        # report had listed as unhit
        aimed = {t for r in biased.cdv.rounds if r.biased for t in r.targeted}
        assert aimed, "no round reported a target"
        assert any(r.biased for r in biased.cdv.rounds)
        assert not any(r.biased for r in control.cdv.rounds)
        # at least one bin the solver named was drawn strictly more often than
        # the unbiased arm drew it — the aiming pulls the distribution toward its
        # targets rather than merely reshuffling it
        pulled = [label for label in aimed
                  if (lambda d, v: d == "tool_condition" and b.get(v, 0) > c.get(v, 0))(
                      *label.split("=", 1))]
        assert pulled, f"aimed at {sorted(aimed)} and drew {b} vs control {c}"

    def test_the_wiring_runs_with_the_network_blocked(self, reg, cfg):
        """The module-level ``no_network`` fixture is the assertion. A closure
        loop that needs an API key is a closure loop nobody runs."""
        out = ops.cdv_op(cfg, reg, _agent(), space=seed_space(), rubric=RUBRIC,
                         run_scenario=scenario_runner(), seed=1,
                         budget=Budget(max_scenarios=5, max_dollars=1.0,
                                       max_rounds=1))
        assert out.cdv.scenarios_run == 5


# --------------------------------------------------------------------------- #
# 3. what the wiring refuses to do
# --------------------------------------------------------------------------- #


class TestTheWiringRefuses:
    def test_cdv_op_refuses_without_a_scenario_runner(self, reg, cfg):
        """Criterion 9. There is no default that flattens a RealizedScenario into
        one ``adapter.run()`` call, and this test is what stops one being added
        later behind a populated convergence leg."""
        with pytest.raises(TypeError):
            ops.cdv_op(cfg, reg, _agent(), space=seed_space(), rubric=RUBRIC)

    def test_convergence_is_not_populated_when_the_detector_never_ran(
            self, reg, cfg, monkeypatch):
        """A flat bug curve from a detector that never ran is the same error
        ``unexercised`` exists to prevent. 20 scenarios, a judge outage on every
        one, no oracle failure — the curve is trivially flat and must NOT be
        reported as convergence."""
        def _outage(*a, **k):
            raise RuntimeError("judge unavailable")
        monkeypatch.setattr(ops, "score_traces_sync", _outage)

        out = ops.cdv_op(cfg, reg, _agent(), space=_toy_space(), rubric=RUBRIC,
                         run_scenario=_stub_runner(), seed=2,
                         budget=Budget(max_scenarios=20, max_dollars=5.0,
                                       max_rounds=2))
        assert out.cdv.scenarios_run == 20
        assert all(r.score.scoring_error for r in out.runs)
        assert out.cdv.distinct_signatures == 0
        assert out.scorecard.signoff["convergence"]["status"] == "not_run"
        assert out.scorecard.signoff["envelope"]["status"] != "populated"

    def test_a_scoring_outage_is_not_a_failure_signature(self, reg, cfg,
                                                         monkeypatch):
        """One scored scenario, one outage. The outage contributes nothing to the
        curve — scoring infrastructure failing is not the agent failing."""
        calls = {"n": 0}
        real = ops.score_traces_sync

        def _flaky(cfg_, reg_, traces, cases, model, **kw):
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                raise RuntimeError("judge unavailable")
            return real(cfg_, reg_, traces, cases, model, **kw)
        monkeypatch.setattr(ops, "score_traces_sync", _flaky)

        out = ops.cdv_op(cfg, reg, _agent(), space=_toy_space(), rubric=RUBRIC,
                         run_scenario=_stub_runner(), seed=2,
                         budget=Budget(max_scenarios=4, max_dollars=5.0,
                                       max_rounds=1))
        errored = [r for r in out.runs if r.score.scoring_error]
        scored = [r for r in out.runs if not r.score.scoring_error]
        assert errored and scored
        assert all(r.failures == [] for r in errored)
        # The outage is still DENIED, not passed — it is frozen for a human with
        # an honest "unknown" signature rather than counted as a bug found.
        frozen = {f.scenario_id: f.signature for f in out.cdv.frozen_regressions}
        for r in errored:
            assert frozen.get(r.scenario.scenario_id) == "unknown"

    def test_no_suite_is_created_and_the_regressions_are_proposals(self, reg, cfg):
        """Criterion 11. Hard Rule 63: failures become permanent tests by a human
        decision, never by a loop deciding for itself."""
        out = ops.cdv_op(cfg, reg, _agent(), space=seed_space(), rubric=RUBRIC,
                         run_scenario=scenario_runner(), seed=7,
                         budget=Budget(max_scenarios=20, max_dollars=5.0,
                                       max_rounds=2))
        with pytest.raises(NotFoundError):
            reg.get_suite("cdv:space-conversational_transactional")
        assert out.cdv.frozen_regressions, "the stand-in agent failed nothing"
        payload = json.loads(open(out.regressions_path).read())
        assert payload["regressions"]
        assert all(r["approved"] is False for r in payload["regressions"])
        assert all(r["scenario"]["expectation"] for r in payload["regressions"])


# --------------------------------------------------------------------------- #
# 4. the budget, and the gate that does NOT move
# --------------------------------------------------------------------------- #


class TestTheBudgetAndTheGate:
    def test_the_budget_counts_judge_spend_as_well_as_agent_spend(
            self, reg, cfg, monkeypatch):
        """``Budget.max_dollars`` is the only ceiling the loop enforces and it is
        charged from ``ExecutionResult.cost_usd``. Counting only the trace would
        leave judge spend uncapped."""
        def _priced(cfg_, reg_, traces, cases, model, **kw):
            return [RunScore(trace_id=t.trace_id, test_id=c.test_id,
                             criterion_scores=[], passed=True,
                             cost_usd=t.total_cost_usd, scoring_cost_usd=0.02)
                    for t, c in zip(traces, cases)]
        monkeypatch.setattr(ops, "score_traces_sync", _priced)

        out = ops.cdv_op(cfg, reg, _agent(), space=_toy_space(), rubric=RUBRIC,
                         run_scenario=_stub_runner(cost=0.01), seed=4,
                         budget=Budget(max_scenarios=6, max_dollars=100.0,
                                       max_rounds=1))
        assert out.cdv.dollars_spent == pytest.approx(0.03 * 6)
        assert out.cdv.dollars_spent == pytest.approx(
            sum(r.trace.total_cost_usd for r in out.runs)
            + sum(r.score.scoring_cost_usd for r in out.runs))

    def test_the_dollar_budget_stops_the_loop(self, reg, cfg, monkeypatch):
        """A hard ceiling that reports partial closure rather than truncating the
        report. The check is AFTER the charge (``cdv.py:273``), so an overshoot of
        at most one scenario is expected and stated."""
        def _priced(cfg_, reg_, traces, cases, model, **kw):
            return [RunScore(trace_id=t.trace_id, test_id=c.test_id,
                             criterion_scores=[], passed=True,
                             cost_usd=t.total_cost_usd, scoring_cost_usd=0.02)
                    for t, c in zip(traces, cases)]
        monkeypatch.setattr(ops, "score_traces_sync", _priced)

        out = ops.cdv_op(cfg, reg, _agent(), space=_toy_space(), rubric=RUBRIC,
                         run_scenario=_stub_runner(cost=0.01), seed=4,
                         budget=Budget(max_scenarios=100, max_dollars=0.09,
                                       max_rounds=4))
        assert out.cdv.stopped_because == "dollar budget exhausted"
        assert out.cdv.scenarios_run <= 4          # 3 scenarios + one overshoot

    def test_convergence_and_envelope_do_not_change_signs_off(self):
        """The anti-overclaim test. ``signs_off`` binds on coverage closed +
        assertions populated with zero violations + zero formal counterexamples +
        no illegal-bin hits, and ``refusal_reasons`` mirrors it condition for
        condition. Neither convergence nor envelope is in that expression, and
        P5 must not be read as having tightened certification."""
        report = collect(baseline_model(cfg=CFG), [Sample(trace=_tool_trace())])
        rich = CDVResult(report=report, scenarios_run=120, dollars_spent=4.0,
                         bug_curve=[(n, min(n, 12)) for n in range(1, 121)],
                         frozen_regressions=[])
        without = build_signoff(signoff_id="s", agent_id="a",
                                agent_config_hash="c", coverage_report=report)
        for result in (rich, CDVResult(report=report)):
            with_cdv = build_signoff(signoff_id="s", agent_id="a",
                                     agent_config_hash="c",
                                     coverage_report=report, cdv_result=result)
            assert with_cdv.signs_off == without.signs_off
            assert with_cdv.refusal_reasons() == without.refusal_reasons()
            assert with_cdv.convergence.status == "populated"
