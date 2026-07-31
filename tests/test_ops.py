"""M1 of the UI build: the shared ops layer (agenttic/ops.py) and the harness
progress hook. Verifies CLI-parity of run_and_score_op against the e2e
expectations and that progress events fire in order with correct payloads.
"""

import asyncio
import json
from pathlib import Path

import pytest

from agenttic import ops
from agenttic.adapters.anthropic_simple import AnthropicSimpleAgent
from agenttic.adapters.blackbox_http import BlackBoxHTTPAgent
from agenttic.harness.runner import HarnessConfig, run_suite
from agenttic.registry.sqlite_store import Registry
from agenttic.registry.store import InMemoryTraceStore
from agenttic.schema.rubric import Rubric
from agenttic.schema.testcase import TestCase, TestSuite
from tests.test_e2e_pipeline import ProfessionalToneJudgeClient, RoutingFakeClient
from tests.test_harness import StubAdapter, make_cases, make_suite

PILOT = Path(__file__).parent.parent / "examples" / "pilot_support_triage"

CFG = {
    # no judge_executor -> plain strong judge (works with .messages fake)
    "models": {"agent_default": "agent-model", "judge_strong": "judge-model",
               "judge_light": "judge-light"},
    "harness": {"timeout_seconds": 10, "max_parallel": 5,
                "transport_retries": 1, "max_steps": 10},
    "scoring": {"calibration_threshold": 0.8},
    "paths": {"review_dir": "review/"},
}


@pytest.fixture
def pilot_registry(tmp_path):
    reg = Registry(tmp_path / "ops.db")
    reg.save_rubric(Rubric.model_validate_json((PILOT / "rubric.json").read_text()))
    suite = TestSuite.model_validate_json((PILOT / "suite.json").read_text())
    cases = [TestCase.model_validate(c)
             for c in json.loads((PILOT / "cases.json").read_text())]
    reg.save_suite(suite, cases)
    reg.approve_suite(suite.suite_id, suite.version)
    return reg, suite.suite_id


class TestRunAndScoreOp:
    def test_cli_parity_with_progress_events(self, pilot_registry):
        reg, suite_id = pilot_registry
        adapter = AnthropicSimpleAgent(model="agent-model",
                                       kb_path=PILOT / "kb.json",
                                       client=RoutingFakeClient(),
                                       agent_id="ref-agent")
        events = []
        sc = asyncio.run(ops.run_and_score_op(
            CFG, reg, adapter, suite_id,
            on_progress=lambda t, d: events.append((t, d)),
            judge_client=ProfessionalToneJudgeClient()))

        assert sc.task_success_rate == pytest.approx(0.8)
        assert reg.get_scorecard(sc.scorecard_id).suite_id == suite_id

        by_type = {}
        for t, d in events:
            by_type.setdefault(t, []).append(d)
        assert len(by_type["case_started"]) == 10
        assert len(by_type["case_finished"]) == 10
        assert len(by_type["case_scored"]) == 10
        assert all(d["total"] == 10 for _, d in events)
        assert all("trace_id" in d for d in by_type["case_finished"])
        scored_ids = {d["test_id"] for d in by_type["case_scored"]}
        assert scored_ids == {f"triage-{i:03d}" for i in range(10)}

    def test_report_op_renders(self, pilot_registry):
        reg, suite_id = pilot_registry
        adapter = AnthropicSimpleAgent(model="agent-model",
                                       kb_path=PILOT / "kb.json",
                                       client=RoutingFakeClient(),
                                       agent_id="ref-agent")
        sc = asyncio.run(ops.run_and_score_op(
            CFG, reg, adapter, suite_id,
            judge_client=ProfessionalToneJudgeClient()))
        md = ops.report_op(reg, sc.scorecard_id)
        assert "Executive summary" in md and "80%" in md


class TestBuildAdapter:
    def test_reference(self):
        a = ops.build_adapter(CFG, variant="reference", agent_id="x",
                              client=RoutingFakeClient())
        assert isinstance(a, AnthropicSimpleAgent)
        assert a.model == "agent-model"

    def test_blackbox(self):
        a = ops.build_adapter(CFG, variant="blackbox", agent_id="x",
                              url="http://h/run")
        assert isinstance(a, BlackBoxHTTPAgent)

    def test_blackbox_requires_url(self):
        with pytest.raises(ops.AgentConfigError, match="URL"):
            ops.build_adapter(CFG, variant="blackbox", agent_id="x")

    def test_managed_requires_ids(self):
        # A managed agent without its IDs is a clean, user-facing config error
        # (AgentConfigError <: ValueError), not an opaque crash.
        with pytest.raises(ops.AgentConfigError, match="managed"):
            ops.build_adapter(CFG, variant="managed", agent_id="x",
                              managed_agent_id="agent_01")

    def test_agent_model_of_blackbox_never_collides(self):
        a = ops.build_adapter(CFG, variant="blackbox", agent_id="cx",
                              url="http://h/run")
        assert ops.agent_model_of(a) == "blackbox:cx"


class TestHarnessProgressHook:
    def test_event_order_and_default_none(self):
        cases, store = make_cases(3), InMemoryTraceStore()
        events = []
        asyncio.run(run_suite(StubAdapter(), make_suite(cases), cases, store,
                              HarnessConfig(max_parallel=1, timeout_seconds=5),
                              on_event=lambda t, d: events.append((t, d))))
        # max_parallel=1 -> strictly interleaved start/finish per case
        types = [t for t, _ in events]
        assert types == ["case_started", "case_finished"] * 3
        assert events[1][1]["ok"] is True
        # default path (no callback) unchanged
        asyncio.run(run_suite(StubAdapter(), make_suite(cases), cases, store))

    def test_failure_case_reports_not_ok(self):
        cases, store = make_cases(1), InMemoryTraceStore()
        adapter = StubAdapter(errors=[RuntimeError("agent bug")])
        events = []
        asyncio.run(run_suite(adapter, make_suite(cases), cases, store,
                              on_event=lambda t, d: events.append((t, d))))
        finished = [d for t, d in events if t == "case_finished"]
        assert finished[0]["ok"] is False


class TestSystemPromptOverride:
    def test_reference_adapter_uses_and_hashes_override(self):
        client = RoutingFakeClient()
        plain = ops.build_adapter(CFG, variant="reference", agent_id="x",
                                  client=client)
        triage = ops.build_adapter(CFG, variant="reference", agent_id="x",
                                   client=client,
                                   system_prompt="Reply ONLY the queue name.")
        assert triage.system_prompt == "Reply ONLY the queue name."
        assert triage.describe()["system_prompt"] == "Reply ONLY the queue name."
        # a prompt change is a config change — attributable across scorecards
        assert triage.config_hash() != plain.config_hash()

    def test_adapter_sends_override_to_the_model(self):
        class CaptureClient:
            def __init__(self):
                from types import SimpleNamespace as NS
                self.calls = []
                self.messages = NS(create=self._create)
            def _create(self, **kw):
                from types import SimpleNamespace as NS
                self.calls.append(kw)
                return NS(stop_reason="end_turn",
                          usage=NS(input_tokens=1, output_tokens=1),
                          content=[NS(type="text", text="billing")])
        cap = CaptureClient()
        adapter = ops.build_adapter(CFG, variant="reference", agent_id="x",
                                    client=cap, system_prompt="ONLY the queue.")
        adapter.run({"ticket": "refund"})
        assert cap.calls[0]["system"] == "ONLY the queue."


class TestPartialBatchScoring:
    def test_one_case_errors_others_still_scored(self, pilot_registry):
        reg, suite_id = pilot_registry

        class FlakyJudgeClient:
            """Raises on the WRONGCASE adversarial cases, scores 1 otherwise."""
            def __init__(self):
                from types import SimpleNamespace as NS
                import json
                self._json = json
                self.messages = NS(create=self._create)
            def _create(self, **kw):
                from types import SimpleNamespace as NS
                text = str(kw.get("messages"))
                if "WRONGCASE" in text or "wrongcase" in text:
                    raise RuntimeError("judge API timeout")
                return NS(content=[NS(type="text",
                          text=self._json.dumps({"score": 1, "rationale": "ok"}))])

        adapter = AnthropicSimpleAgent(model="agent-model",
                                       kb_path=PILOT / "kb.json",
                                       client=RoutingFakeClient(),
                                       agent_id="ref-agent")
        events = []
        sc = asyncio.run(ops.run_and_score_op(
            CFG, reg, adapter, suite_id,
            on_progress=lambda t, d: events.append((t, d)),
            judge_client=FlakyJudgeClient()))

        # the two WRONGCASE cases error during scoring; the batch survives
        assert set(sc.errored_test_ids) == {"triage-008", "triage-009"}
        scored = [r for r in sc.run_scores if r.scoring_error is None]
        assert len(scored) == 8
        # success rate is over the SCORED subset (8), not 10
        assert sc.task_success_rate == 1.0
        # errored cases are kept, marked, excluded from criterion means
        errored = [r for r in sc.run_scores if r.scoring_error]
        assert len(errored) == 2 and all("judge API timeout" in r.scoring_error
                                         for r in errored)
        assert "routing" in sc.per_criterion_means  # computed over scored only
        # cost still counts all 10 runs (the agent ran regardless)
        assert sc.mean_cost_usd > 0
        case_errors = [d for t, d in events if t == "case_error"]
        assert len(case_errors) == 2


class TestAggregatePartial:
    def _run(self, test_id, passed, error=None):
        from agenttic.schema.scorecard import CriterionScore, RunScore
        crits = [] if error else [CriterionScore(
            criterion_id="x", score=1.0 if passed else 0.0, scorer="code")]
        return RunScore(trace_id=f"t-{test_id}", test_id=test_id,
                        criterion_scores=crits, passed=passed,
                        cost_usd=0.01, latency_ms=100.0, scoring_error=error)

    def test_rates_exclude_errored_cost_includes_all(self):
        from agenttic.schema.scorecard import Scorecard
        runs = [self._run("a", True), self._run("b", False),
                self._run("c", False, error="JudgeError: boom")]
        sc = Scorecard.aggregate(
            scorecard_id="s", agent_id="ag", suite_id="su", suite_version=1,
            rubric_id="r", rubric_version=1, run_scores=runs,
            visibility_tier="glass_box")
        assert sc.errored_test_ids == ["c"]
        assert sc.task_success_rate == 0.5          # 1 of 2 scored
        assert sc.per_criterion_means == {"x": 0.5}  # over scored only
        assert sc.mean_cost_usd == pytest.approx(0.01)  # all 3 runs
        assert len(sc.run_scores) == 3              # errored kept

    def test_all_errored_does_not_crash(self):
        from agenttic.schema.scorecard import Scorecard
        runs = [self._run("a", False, error="x"), self._run("b", False, error="y")]
        sc = Scorecard.aggregate(
            scorecard_id="s", agent_id="ag", suite_id="su", suite_version=1,
            rubric_id="r", rubric_version=1, run_scores=runs,
            visibility_tier="glass_box")
        assert sc.task_success_rate == 0.0 and sc.per_criterion_means == {}
        assert set(sc.errored_test_ids) == {"a", "b"}


class TestVerifyOpRefusesNonResults:
    """A run the adapter could not complete must verify against nothing.

    ``verify_op`` used to hand every trace to both legs. A harness failure carries
    ``final_output="HARNESS_FAILURE:transport"`` and one ``error`` span, so the
    coverage model read the marker string as an answer and the error text as
    environment content, and the assertion battery scored
    ``never_secret_in_output`` PASS because a marker string contains no secret.
    Neither is evidence that anything happened.

    The exclusion is checked in both directions here: the numbers must not move,
    AND the summary must say how many runs it left out — a silent filter would be
    the same over-report wearing a different hat.
    """

    @staticmethod
    def _traces():
        from tests.coverage.test_nonresult_samples import (harness_failure,
                                                           recovered_run)
        return [recovered_run() for _ in range(3)], [
            harness_failure(), harness_failure("timeout", "run exceeded 30s")]

    def test_non_results_move_no_coverage_number(self):
        real, dead = self._traces()
        _a, clean = ops.verify_op(real)
        _b, mixed = ops.verify_op(real + dead)
        assert mixed["trace_closure"] == clean["trace_closure"]
        assert mixed["per_coverpoint"] == clean["per_coverpoint"]
        assert mixed["other_drift"] == clean["other_drift"]
        assert mixed["holes"] == clean["holes"]

    def test_the_summary_discloses_what_it_left_out(self):
        real, dead = self._traces()
        _a, mixed = ops.verify_op(real + dead)
        assert mixed["samples"] == 3
        assert mixed["samples_submitted"] == 5
        assert mixed["non_results"] == 2
        assert mixed["non_result_reasons"] == {
            "HARNESS_FAILURE:transport": 1, "HARNESS_FAILURE:timeout": 1}

    def test_a_clean_batch_reports_no_exclusions(self):
        real, _dead = self._traces()
        _a, clean = ops.verify_op(real)
        assert clean["non_results"] == 0 and clean["samples"] == 3
        assert clean["samples_submitted"] == 3

    def test_assertions_are_not_evaluated_against_a_run_that_never_happened(self):
        """Every case died in transport: nothing was verified, and the sign-off's
        assertion leg must say ``not_run`` rather than report a phantom pass."""
        _real, dead = self._traces()
        results, summary = ops.verify_op(dead)
        assert results == []
        assert "assertions" not in summary
        assert summary["signoff"]["assertions"]["status"] == "not_run"
        assert summary["non_results"] == 2

    def test_a_real_run_still_gets_its_assertions(self):
        real, dead = self._traces()
        results, summary = ops.verify_op(real + dead)
        # 8 built-in properties x 3 REAL traces — the two dead ones contribute none
        assert len(results) == len(ops.verify_op(real)[0])
        assert summary["assertions"]["total"] == 8

    def test_the_signed_coverage_leg_carries_the_unpolluted_closure(self):
        """The sign-off is the signed artifact. If the leg disagreed with the
        report, the signed number would be the one that was wrong."""
        real, dead = self._traces()
        _a, clean = ops.verify_op(real)
        _b, mixed = ops.verify_op(real + dead)
        assert (mixed["signoff"]["coverage"]["trace_closure"]
                == clean["signoff"]["coverage"]["trace_closure"])


# --------------------------------------------------------------------------- #
# cdv_op keeps the runs it drove
# --------------------------------------------------------------------------- #


CDV_CFG = {
    "models": {"agent_default": "scripted-support", "judge_strong": "judge-model"},
    "harness": {"timeout_seconds": 10, "max_parallel": 4, "transport_retries": 1,
                "max_steps": 8},
    "scoring": {"max_parallel": 4},
    "coverage": {"closure_target": 0.95},
    "cdv": {"batch_size": 10},
}

CDV_RUBRIC = Rubric(rubric_id="r-cdv-persist", version=1, criteria=[
    {"criterion_id": "step_budget", "description": "within the step budget",
     "scorer": "code", "scale": "binary", "check_ref": "steps_under_limit"},
    {"criterion_id": "cost_budget", "description": "within the cost budget",
     "scorer": "code", "scale": "binary", "check_ref": "cost_under_limit"}])


def _cdv_reg(tmp_path, cls=None):
    from agenttic.registry.sqlite_store import Registry as _Registry
    reg = (cls or _Registry)(str(tmp_path / "cdv-persist.db"))
    reg.save_rubric(CDV_RUBRIC)
    return reg


def _cdv_cfg(tmp_path):
    return {**CDV_CFG, "paths": {"review_dir": str(tmp_path / "review")}}


def _scripted_agent(agent_id="cdv-persist"):
    from agenttic.scenario.runner import ScenarioAgent, ScriptedSupportClient
    return ScenarioAgent(model="scripted-support", client=ScriptedSupportClient(),
                         agent_id=agent_id, max_steps=8)


def _run_cdv(reg, cfg, *, seed=5, scenarios=12, rounds=2, on_progress=None,
             agent_id="cdv-persist"):
    """A real offline CDV: scripted agent, code-only rubric, no key, no network."""
    from agenttic.scenario.runner import scenario_runner
    from agenttic.stimulus.spaces.conversational_transactional import seed_space
    from agenttic.verification.cdv import Budget
    return ops.cdv_op(cfg, reg, _scripted_agent(agent_id), space=seed_space(),
                      rubric=CDV_RUBRIC, run_scenario=scenario_runner(cfg=None),
                      seed=seed, on_progress=on_progress,
                      budget=Budget(max_scenarios=scenarios, max_dollars=5.0,
                                    max_rounds=rounds))


def _row_count(reg) -> int:
    import sqlite3
    con = sqlite3.connect(str(reg.engine.url.database))
    try:
        return con.execute("select count(*) from scenario_runs").fetchone()[0]
    finally:
        con.close()


class TestCDVOpPersistsItsRuns:
    """``agenttic cdv`` drove real scenarios through a real world and threw the
    record away.

    ``Registry.save_scenario_run`` had two callers — ``cli.py``'s single-scenario
    command and the tests — so the loop that runs sixty scenarios against an
    agent kept none of them: transcript, fault report, state diff and blocked
    calls died with the process, and ``/app/scenarios`` showed its empty state
    forever in any real deployment. These tests are that gap, closed.
    """

    def test_every_executed_scenario_becomes_a_row(self, tmp_path):
        reg, cfg = _cdv_reg(tmp_path), _cdv_cfg(tmp_path)
        assert _row_count(reg) == 0
        out = _run_cdv(reg, cfg)
        assert out.cdv.scenarios_run == 12
        assert _row_count(reg) == out.cdv.scenarios_run
        stored = reg.list_scenario_runs()
        assert ({r["run_id"] for r in stored}
                == {r.trace.trace_id for r in out.runs})
        assert {r["scenario_id"] for r in stored} == {
            r.scenario.scenario_id for r in out.runs}

    def test_the_evidence_survives_the_process(self, tmp_path):
        """The row is only worth writing if what died with the process comes
        back: the fault report, the state diff, the blocked calls, and a trace
        that still resolves."""
        reg, cfg = _cdv_reg(tmp_path), _cdv_cfg(tmp_path)
        out = _run_cdv(reg, cfg)
        rows = [reg.get_scenario_run(r.trace.trace_id) for r in out.runs]
        for row, run in zip(rows, out.runs):
            assert row["state_diff"] == run.outcome.state_diff
            assert row["blocked"] == run.outcome.blocked
            assert row["faults"]["planned"] == run.outcome.fault_report["planned"]
            assert row["faults"]["fired"] == run.outcome.fault_report["fired"]
            assert reg.get_trace(row["trace_id"]).trace_id == row["trace_id"]
        # not vacuous: this batch really did stage faults and change the world
        assert any(r["faults"]["planned"] for r in rows)
        assert any(r["state_diff"] for r in rows)

    def test_list_rows_stay_a_list(self, tmp_path):
        """A list does not load a report per row — the coverage block is on the
        detail view only, and persisting from the loop must not change that."""
        reg, cfg = _cdv_reg(tmp_path), _cdv_cfg(tmp_path)
        _run_cdv(reg, cfg, scenarios=4, rounds=1)
        rows = reg.list_scenario_runs()
        assert len(rows) == 4          # never vacuous over an empty list
        for row in rows:
            assert "coverage" not in row
            assert "divergence" not in row and "turns" not in row


class TestWhatTheStoredCoverageIsAllowedToClaim:
    def test_the_bins_are_the_measurable_ones_not_the_raw_counter(self, tmp_path):
        """The trap. ``BinCoverage.trace_hits`` counts a predicate firing;
        ``countable()`` + ``exhibited()`` are where the model's own measurability
        lands. ``session_shape`` is declared NOT measurable — "a trace with no
        turn markers is evidence of absent instrumentation, not of a single-turn
        session" — and its ``single_turn`` extractor is ``_human_turns <= 1``,
        which is True at ZERO turns. So the raw counter credits it off every
        single-shot trace ever written, and storing that would file missing
        instrumentation under the words *what the run EXHIBITED*.

        Both halves are asserted: the raw counter really does credit it on this
        very sample, and the stored row really does not.
        """
        from agenttic.coverage.collect import collect
        from agenttic.coverage.models.baseline import baseline_model
        from agenttic.scenario.runner import exhibited_bin_ids

        reg, cfg = _cdv_reg(tmp_path), _cdv_cfg(tmp_path)
        out = _run_cdv(reg, cfg, scenarios=6, rounds=1)
        model = baseline_model(cfg=cfg)

        raw_credited, ever_stored = set(), set()
        for run in out.runs:
            report = collect(model, [run.sample()])
            raw_credited |= {f"{cp_id}:{b.bin_id}"
                             for cp_id, cov in report.coverpoints.items()
                             for b in cov.bins.values() if b.trace_hits}
            stored = reg.get_scenario_run(run.trace.trace_id)["coverage"]["bins"]
            assert stored == exhibited_bin_ids(report)
            ever_stored |= set(stored)

        assert "session_shape:single_turn" in raw_credited, (
            "the raw counter no longer credits the unmeasurable bin — if the "
            "coverage model changed, this test is pinning nothing")
        assert not any(b.startswith("session_shape:") for b in ever_stored)
        assert raw_credited - ever_stored

    def test_divergence_is_recorded_and_keeps_its_two_measured_states(
            self, tmp_path):
        """``[]`` and ``[...]`` are both measurements and must not look alike:
        one says every corner the point asked for appeared, the other names the
        corners it did not. Seed 5 over 12 scenarios produces both.
        """
        reg, cfg = _cdv_reg(tmp_path), _cdv_cfg(tmp_path)
        out = _run_cdv(reg, cfg)
        rows = [reg.get_scenario_run(r.trace.trace_id)["coverage"]
                for r in out.runs]
        assert all(r["divergence"] is not None for r in rows)   # all RECORDED
        assert any(r["divergence"] == [] for r in rows)
        found = [d for r in rows for d in r["divergence"]]
        assert found
        for d in found:
            assert set(d) == {"coverpoint_id", "bin_id", "requested", "exhibited"}
            assert d["exhibited"] == 0 and d["requested"] >= 1

    def test_the_stored_rows_agree_with_the_loops_own_report(self, tmp_path):
        """One coverage model, threaded in — not a second one built here. Every
        divergence row a stored run carries is a bin the loop's own report also
        says was requested and never exhibited."""
        reg, cfg = _cdv_reg(tmp_path), _cdv_cfg(tmp_path)
        out = _run_cdv(reg, cfg)
        report = out.cdv.report
        for run in out.runs:
            cov = reg.get_scenario_run(run.trace.trace_id)["coverage"]
            for d in cov["divergence"]:
                b = report.coverpoints[d["coverpoint_id"]].bins[d["bin_id"]]
                assert b.stimulus_hits >= d["requested"]
            for label in cov["bins"]:
                cp_id, bin_id = label.split(":", 1)
                assert report.coverpoints[cp_id].bins[bin_id].trace_hits > 0

    def test_no_coverage_model_stores_not_recorded_never_empty(self, tmp_path):
        """An executor with no coverage model computed nothing, and the row says
        so. ``None`` is *nobody looked*; ``[]`` would claim a measurement that
        credited nothing. Every existing caller of ``harness_executor`` is in
        this state, so this is the shape they write."""
        from agenttic.scenario.runner import harness_executor, scenario_runner
        from agenttic.scenario.tools import RETAIL_POLICY
        from agenttic.stimulus.realize import realize
        from agenttic.stimulus.space import sample_point
        from agenttic.stimulus.spaces.conversational_transactional import (
            seed_space)

        reg, cfg = _cdv_reg(tmp_path), _cdv_cfg(tmp_path)
        space = seed_space()
        scn = realize(sample_point(space, 5), 5, space, policy=RETAIL_POLICY)
        execute, runs = harness_executor(
            cfg, reg, _scripted_agent(), rubric=CDV_RUBRIC,
            run_scenario=scenario_runner(cfg=None), suite_id="s-nocov")
        execute(scn)

        cov = reg.get_scenario_run(runs[0].trace.trace_id)["coverage"]
        # `model` joined this block: a bin id is only interpretable against the
        # model that names it, so the store records which one produced the list.
        # No model was offered here, so it is None — the same "nobody said" the
        # other three keys carry.
        assert cov == {"measured": False, "bins": None, "divergence": None,
                       "model": None}


class TestAStorageFailureNeverCostsTheRun:
    """Contained, and not swallowed. A run that drove twelve scenarios, scored
    them and closed coverage has already produced its result; losing that to a
    failed INSERT would trade an evidence-keeping failure for an
    evidence-destroying one. But a silent loss is worse than a loud one — the
    console would print a full CDV report over rows that do not exist."""

    class _NoWrite:
        def __init__(self, exc):
            self.exc = exc

        def __call__(self, *a, **k):
            raise self.exc

    def test_the_run_survives_and_says_so(self, tmp_path, caplog,
                                          monkeypatch):
        import logging as _logging

        reg, cfg = _cdv_reg(tmp_path), _cdv_cfg(tmp_path)
        monkeypatch.setattr(type(reg), "save_scenario_run",
                            self._NoWrite(OSError("disk I/O error")),
                            raising=True)
        events = []
        with caplog.at_level(_logging.WARNING, logger="agenttic.scenario.runner"):
            out = _run_cdv(reg, cfg, scenarios=4, rounds=1,
                           on_progress=lambda t, d: events.append((t, d)))

        # the run itself is untouched
        assert out.cdv.scenarios_run == 4 and len(out.runs) == 4
        assert out.scorecard.scorecard_id
        assert _row_count(reg) == 0
        # and the loss is visible, twice: on stderr via the logger, and on the
        # progress stream a server/UI caller consumes
        not_stored = [d for t, d in events if t == "scenario_run_not_stored"]
        assert len(not_stored) == 4
        assert all("OSError: disk I/O error" == d["error"] for d in not_stored)
        assert {d["trace_id"] for d in not_stored} == {
            r.trace.trace_id for r in out.runs}
        warnings = [rec for rec in caplog.records
                    if rec.name == "agenttic.scenario.runner"]
        assert len(warnings) == 4
        assert "NOT STORED" in warnings[0].getMessage()
        # the scenarios still executed — the two events are not confused
        assert len([t for t, _ in events if t == "scenario_executed"]) == 4

    def test_an_already_stored_run_is_not_reported_as_a_failure(
            self, tmp_path, caplog):
        """``DuplicateVersionError`` means the row is already there — one run is
        one trace. ``ops.cdv_op`` treats ``save_scenario_space`` the same way."""
        import logging as _logging

        from agenttic.registry.sqlite_store import DuplicateVersionError
        from agenttic.scenario.runner import persist_scenario_run

        reg, cfg = _cdv_reg(tmp_path), _cdv_cfg(tmp_path)
        out = _run_cdv(reg, cfg, scenarios=2, rounds=1)
        run = out.runs[0]
        events = []
        with caplog.at_level(_logging.WARNING, logger="agenttic.scenario.runner"):
            with pytest.raises(DuplicateVersionError):
                reg.save_scenario_run(run.scenario, run.outcome)
            assert persist_scenario_run(
                reg, run, on_progress=lambda t, d: events.append((t, d))) == ""
        assert events == []
        assert [r for r in caplog.records
                if r.name == "agenttic.scenario.runner"] == []
        assert _row_count(reg) == 2

    def test_a_coverage_failure_loses_the_coverage_not_the_row(self, tmp_path,
                                                               caplog):
        """The narrower containment. A model that raises must not cost the run
        its record — and the row it leaves says NOT RECORDED rather than reading
        as a clean measurement that credited nothing."""
        import logging as _logging

        from agenttic.scenario.runner import harness_executor, scenario_runner
        from agenttic.scenario.tools import RETAIL_POLICY
        from agenttic.stimulus.realize import realize
        from agenttic.stimulus.space import sample_point
        from agenttic.stimulus.spaces.conversational_transactional import (
            seed_space)

        class _BrokenModel:
            def validate_against_registry(self):
                raise RuntimeError("model is broken")

        reg, cfg = _cdv_reg(tmp_path), _cdv_cfg(tmp_path)
        space = seed_space()
        scn = realize(sample_point(space, 7), 7, space, policy=RETAIL_POLICY)
        execute, runs = harness_executor(
            cfg, reg, _scripted_agent(), rubric=CDV_RUBRIC,
            run_scenario=scenario_runner(cfg=None), suite_id="s-brokencov",
            coverage_model=_BrokenModel())
        with caplog.at_level(_logging.WARNING, logger="agenttic.scenario.runner"):
            execute(scn)

        assert _row_count(reg) == 1                    # the row survived
        cov = reg.get_scenario_run(runs[0].trace.trace_id)["coverage"]
        # STRONGER than before, not weaker. The measurement is still absent —
        # `measured: False`, both lists None, exactly as this test always
        # required. What is new is that the broken model is DISCLOSED rather
        # than silently dropped: a producer that handed the store something that
        # is not a coverage model leaves a reason a reader can see.
        assert cov["measured"] is False
        assert cov["bins"] is None and cov["divergence"] is None
        assert cov["model"]["ref"] is None
        assert "_BrokenModel" in cov["model"]["problem"]
        assert any("NOT RECORDED" in r.getMessage() for r in caplog.records
                   if r.name == "agenttic.scenario.runner")


class TestTheHarnessBatteryIsOffUntilItIsAskedFor:
    """P7's last mile. `redteam/honeypot.py` always distinguished `resisted` (a
    fact about the MODEL) from `attempted_blocked` (a fact about the HARNESS),
    and `report_op` always rendered a stored battery — but nothing ran one
    against a real agent, so the distinction lived in dev tooling.

    It spends money on every run it is enabled for, so the default is OFF, and
    the off-state has to be provably SILENT rather than provably reassuring.
    """

    def test_off_by_default_runs_nothing(self, tmp_path, monkeypatch):
        from agenttic import ops

        called = []
        monkeypatch.setattr(
            "agenttic.redteam.honeypot.run_honeypot_harness",
            lambda *a, **k: called.append(1))
        reg = _cdv_reg(tmp_path)
        # no `harness` block at all, and a `harness` block that omits the key
        for cfg in ({}, {"harness": {"max_steps": 8}}):
            ops._run_honeypot_battery(cfg, reg, _scripted_agent(), object())
        assert called == [], "the battery ran without being asked for"

    def test_an_explicit_false_runs_nothing(self, tmp_path, monkeypatch):
        from agenttic import ops

        called = []
        monkeypatch.setattr(
            "agenttic.redteam.honeypot.run_honeypot_harness",
            lambda *a, **k: called.append(1))
        ops._run_honeypot_battery({"harness": {"honeypot_battery": False}},
                                  _cdv_reg(tmp_path), _scripted_agent(), object())
        assert called == []

    def test_off_stores_no_battery_so_the_report_says_nothing(self, tmp_path):
        """`report_op`'s documented rule: no battery stored means NO harness
        section — deliberately not a synthesised NOT MEASURED one, which would
        have to invent a posture and a decoy list and would read as "we tested
        the harness and it was inconclusive" when nothing tested it.

        NOT MEASURED is the verdict for a battery that RAN and reached the
        enforcement path zero times. "We did not run one" is a different claim.
        """
        from agenttic import ops

        reg = _cdv_reg(tmp_path)
        ops._run_honeypot_battery({"harness": {"honeypot_battery": False}},
                                  reg, _scripted_agent(), object())
        assert reg.find_honeypot_battery("sc-anything") is None

    def test_an_uninstrumentable_agent_never_costs_the_run(self, tmp_path,
                                                            caplog):
        """`guarded_twin` accepts only `AnthropicSimpleAgent`. A scripted
        scenario agent is not one, so with the flag ON this hits
        `AgentNotInstrumentable` — which is a fact about coverage of the harness,
        not a crash, and must not retract a scorecard that already exists.
        """
        import logging as _logging

        from agenttic import ops

        class _SC:
            scorecard_id = "sc-uninstrumentable"

        reg = _cdv_reg(tmp_path)
        with caplog.at_level(_logging.WARNING, logger="agenttic.ops"):
            ops._run_honeypot_battery({"harness": {"honeypot_battery": True}},
                                      reg, _scripted_agent(), _SC())
        # it did not raise, it stored nothing, and it SAID so
        assert reg.find_honeypot_battery("sc-uninstrumentable") is None
        assert any("harness battery NOT RUN" in r.getMessage()
                   or "harness battery FAILED" in r.getMessage()
                   for r in caplog.records)

    def test_the_shipped_config_ships_it_off(self):
        """The default a customer gets. On would spend money on every run."""
        from agenttic.config import load_config
        cfg = load_config("config.yaml")
        assert cfg["harness"]["honeypot_battery"] is False
