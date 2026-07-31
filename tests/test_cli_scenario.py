"""The operator path to ONE scenario run — and to a run that already happened.

`agenttic cdv` could run hundreds of scenarios and report closure over the batch.
Nothing could run ONE and show what happened to it, and nothing at all could read
a past run back: `Registry.save_scenario_run` had no caller in `src/`, so the
transcript, the fault report, the state diff and the calls the gateway refused
were assembled by `scenario/runner.py` and dropped on the floor when the process
exited. These tests pin the three commands that close that gap.

What is on trial is not that the commands exit 0 — a command that printed nothing
would pass that — but that they keep the distinctions the artifacts keep:

* **a staged fault that never fired is a finding, not a silence.** `never_reached`
  ("we staged a timeout and the agent never called that tool") and `skipped` ("it
  reached the call and could not fire, here is why") are two different facts and
  neither is `fired`;
* **an unchanged world says so in words**, rather than rendering as an empty
  section a reader will skim past;
* **coverage is credited from the TRACE.** A bin the point asked for and the run
  never produced is printed as a divergence, never as coverage;
* **absence stays absent**: a run that stored no fault report reads differently
  from a run that staged nothing, at both the list and the detail surface.

Everything here runs OFFLINE with no API key, under a socket block on the run
path — a command that needs a key to demonstrate itself is a command nobody in CI
can demonstrate.
"""

from __future__ import annotations

import dataclasses
import re
import socket

import pytest
from typer.testing import CliRunner

from agenttic.cli import app
from agenttic.registry.sqlite_store import Registry
from tests.conftest import plain

runner = CliRunner()

#: `--seed 7 --intent out_of_scope --tool-condition timeout`: the point stages a
#: timeout on `lookup_order` and the stand-in escalates an out-of-scope request
#: without ever looking an order up. So the fault is NEVER REACHED — the exact
#: sentence a fault report exists to be able to say.
NEVER_REACHED = ("--seed", "7", "--intent", "out_of_scope",
                 "--tool-condition", "timeout")

#: `--seed 6 --intent refund --tool-condition malformed_response`: the draw lands
#: on `data_condition=entity_not_found`, so the call the fault is staged on fails
#: before there is a response to corrupt. The fault reaches its call and SKIPS,
#: with a reason.
SKIPPED = ("--seed", "6", "--intent", "refund",
           "--tool-condition", "malformed_response")

#: `--seed 3 --intent refund --tool-condition timeout`: the stand-in does look
#: the order up, so this one FIRES.
FIRED = ("--seed", "3", "--intent", "refund", "--tool-condition", "timeout")


@pytest.fixture(autouse=True)
def _fixed_width(monkeypatch):
    """Pin the console width so a wrapped line is not a flaky assertion.

    Rich reads COLUMNS on every print, so a developer's terminal would otherwise
    decide where these lines break.
    """
    monkeypatch.setenv("COLUMNS", "100")


@pytest.fixture
def cfg(tmp_path):
    """A config whose registry is this test's own file."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l, generator: g}\n"
        "harness: {timeout_seconds: 1, max_steps: 8, max_parallel: 1, "
        "transport_retries: 0}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "coverage: {closure_target: 0.95}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path / 'p.db'}, review_dir: "
        f"{tmp_path / 'r'}, calibration_dir: {tmp_path / 'c'}}}\n",
        encoding="utf-8")
    return path


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "p.db")


@pytest.fixture
def no_network(monkeypatch):
    """A run that reaches the network is not the offline run this promises."""
    def _boom(*a, **k):
        raise AssertionError("network access attempted by an offline command")
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def run_cli(cfg, *args):
    return runner.invoke(app, [*args, "--config", str(cfg)])


def scenario_run(cfg, *args):
    r = run_cli(cfg, "scenario", "run", *args)
    assert r.exit_code == 0, r.output
    return plain(r.output)


def stored_run_id(reg) -> str:
    """The newest stored run, read from the registry rather than scraped out of
    the output — the rendering is what other tests are checking."""
    runs = reg.list_scenario_runs()
    assert runs, "the run command stored nothing"
    return runs[0]["run_id"]


# --------------------------------------------------------------------------- #
# the commands exist and are reachable
# --------------------------------------------------------------------------- #


class TestTheCommandsExist:
    def test_the_sub_typer_is_registered(self):
        r = runner.invoke(app, ["scenario", "--help"])
        assert r.exit_code == 0, r.output
        out = plain(r.output)
        assert "run" in out and "transcript" in out and "list" in out

    def test_the_default_dut_is_named_as_a_stand_in_not_a_model(self, cfg,
                                                                no_key,
                                                                no_network):
        """Offline by default, and SAID so. A scripted stand-in described as a
        model would be the fabrication rule broken at the friendliest moment."""
        out = scenario_run(cfg, *NEVER_REACHED)
        assert "NOT a model" in out

    def test_a_model_run_refuses_without_a_key_instead_of_crashing(self, cfg,
                                                                   no_key):
        r = run_cli(cfg, "scenario", "run", "--model", "claude-whatever")
        assert r.exit_code != 0
        out = plain(r.output)
        assert "ANTHROPIC_API_KEY" in out
        assert "Traceback" not in out


# --------------------------------------------------------------------------- #
# the run outlives the process
# --------------------------------------------------------------------------- #


class TestTheRunIsPersisted:
    def test_a_run_is_stored_and_can_be_read_back(self, cfg, reg, no_network):
        """The gap P7 exists to close: run, then find it again from a second
        process (a second CliRunner invocation is exactly that)."""
        first = scenario_run(cfg, *NEVER_REACHED, "--agent", "cli-bot")
        run_id = stored_run_id(reg)
        # the id is printed WHOLE — it is the one value an operator must copy
        assert run_id in first

        listed = run_cli(cfg, "scenario", "list")
        assert listed.exit_code == 0, listed.output
        assert run_id in plain(listed.output)

        back = run_cli(cfg, "scenario", "transcript", run_id)
        assert back.exit_code == 0, back.output
        out = plain(back.output)
        assert reg.get_scenario_run(run_id)["ticket"][:40] in out
        assert "cli-bot" in out

    def test_the_run_reports_the_ticket_and_the_calls_it_produced(self, cfg,
                                                                  no_network):
        out = scenario_run(cfg, *FIRED)
        assert "ticket" in out
        assert "money back" in out                     # the realized ticket text
        assert "lookup_order" in out                   # the tool table
        assert "issue_refund" in out
        assert "executed" in out                       # the gateway's verdict

    def test_an_empty_registry_says_so_rather_than_printing_an_empty_table(
            self, cfg):
        r = run_cli(cfg, "scenario", "list")
        assert r.exit_code == 0, r.output
        assert "No scenario runs stored" in plain(r.output)

    def test_the_agent_filter_is_wired_to_the_query(self, cfg, no_network):
        """An option that narrows nothing is a knob wired to nothing."""
        scenario_run(cfg, *NEVER_REACHED, "--agent", "alpha-bot")
        scenario_run(cfg, *FIRED, "--agent", "beta-bot")
        only = plain(run_cli(cfg, "scenario", "list", "--agent",
                             "alpha-bot").output)
        assert "alpha-bot" in only
        assert "beta-bot" not in only

    def test_the_list_row_says_whether_the_world_moved(self, cfg, no_network):
        scenario_run(cfg, *FIRED, "--agent", "mover")
        scenario_run(cfg, *NEVER_REACHED, "--agent", "still")
        out = plain(run_cli(cfg, "scenario", "list").output)
        assert "world changed" in out
        assert "world unchanged" in out


# --------------------------------------------------------------------------- #
# a staged fault that never fired
# --------------------------------------------------------------------------- #


class TestAStagedFaultThatNeverFired:
    """The acceptance criterion: "we staged a timeout and the agent never called
    that tool" is a FINDING, and a report that omits it lets the run read as
    "the world behaved"."""

    def test_a_never_reached_fault_is_printed_with_what_was_staged(
            self, cfg, no_network):
        out = scenario_run(cfg, *NEVER_REACHED)
        assert "NEVER REACHED" in out
        assert "timeout on call #1 of lookup_order" in out
        assert "never made that call" in out

    def test_it_is_not_reported_as_fired(self, cfg, no_network):
        out = scenario_run(cfg, *NEVER_REACHED)
        assert "FIRED" not in out

    def test_the_staged_count_is_printed_beside_it(self, cfg, no_network):
        """`1 staged` and no fired line is the whole finding; a report that
        printed only the empty `fired` list would look like a clean run."""
        assert "1 staged" in scenario_run(cfg, *NEVER_REACHED)

    def test_a_skipped_fault_carries_the_reason_it_could_not_fire(
            self, cfg, no_network):
        out = scenario_run(cfg, *SKIPPED)
        assert "SKIPPED" in out
        assert "nothing to malform" in out             # the injector's reason
        assert "NEVER REACHED" not in out              # it DID reach its call

    def test_a_fault_that_fired_says_fired(self, cfg, no_network):
        out = scenario_run(cfg, *FIRED)
        assert "FIRED" in out
        assert "NEVER REACHED" not in out
        assert "SKIPPED" not in out

    def test_a_call_the_injector_broke_is_not_reported_as_one_the_harness_blocked(
            self, cfg, no_network):
        """Enforcement is THREE-valued in the world (`scenario/env.py`):
        `faulted` is a call the gateway ALLOWED and the environment then broke.
        Rendering it as `blocked` would credit the harness with a refusal it
        never made, and rendering it as `executed` would hide the fault."""
        out = scenario_run(cfg, *FIRED)
        assert "faulted" in out
        assert "BLOCKED" not in out
        assert "no call was refused" in out

    def test_the_distinction_survives_storage(self, cfg, reg, no_network):
        """The rendering is not the only thing that has to hold: read the same
        run back through `transcript` and the staged-but-unfired fault is still
        a never-reached one."""
        scenario_run(cfg, *NEVER_REACHED)
        r = run_cli(cfg, "scenario", "transcript", stored_run_id(reg))
        assert r.exit_code == 0, r.output
        out = plain(r.output)
        assert "NEVER REACHED" in out
        assert "timeout on call #1 of lookup_order" in out

    def test_no_fault_report_reads_differently_from_no_fault_staged(
            self, cfg, reg, no_network):
        """A run that stored no report did not stage nothing — and printing `0`
        for it would be the vacuity rule inverted at the summary line."""
        run_id = _store_run_without_a_fault_report(reg)

        detail = plain(run_cli(cfg, "scenario", "transcript", run_id).output)
        assert "no fault report was recorded" in detail

        listed = plain(run_cli(cfg, "scenario", "list").output)
        assert "fault report not recorded" in listed
        assert "faults 0/0 fired" not in listed

    def test_a_report_that_would_not_rebuild_is_not_a_report_nobody_wrote(
            self, cfg, reg, no_network):
        """The THIRD state at the summary line. `_faults_view` returns
        ``recorded: True`` with ``counts: None`` for a report that IS stored and
        could not be reconstructed on read, and keeps it apart from
        ``recorded: False`` on purpose. The list printed both as "fault report
        not recorded": a false claim about a record that is in the payload, and
        the exact opposite of what `transcript` says about the same run.
        """
        run_id = _store_run_with_an_unreadable_fault_report(reg)

        detail = plain(run_cli(cfg, "scenario", "transcript", run_id).output)
        assert "could not be reconstructed" in detail
        assert "no fault report was recorded" not in detail

        listed = _flat(run_cli(cfg, "scenario", "list").output)
        assert "fault report could not be reconstructed" in listed
        # the false claim, in either wording the list can produce
        assert "not recorded" not in listed
        assert "faults 0/0 fired" not in listed

    def test_the_list_tells_skipped_apart_from_never_reached(
            self, cfg, reg, no_network):
        """The FOURTH state, at the summary line.

        `{fired}/{planned}` renders `0/1 fired` for a fault that reached its call
        and could not happen AND for one the agent never got to. Those are two
        different facts about the harness — one says the world refused, the other
        says the agent never went there — and the detail view has always kept
        them apart. The list is the surface an operator scans, so collapsing them
        there is where it actually costs someone something.
        """
        scenario_run(cfg, *SKIPPED)
        scenario_run(cfg, *NEVER_REACHED)
        scenario_run(cfg, *FIRED)

        listed = _flat(run_cli(cfg, "scenario", "list").output)
        assert "1 skipped" in listed
        assert "1 never reached" in listed
        assert "1/1 fired" in listed
        # and the two zero-fired rows are no longer the same sentence
        assert listed.count("0/1 fired, 1 skipped") == 1
        assert listed.count("0/1 fired, 1 never reached") == 1

    def test_an_empty_plan_beside_a_fired_fault_is_not_a_quiet_world(
            self, cfg, reg, no_network):
        """"Nothing was staged — the world was left to behave" was asserted from
        `planned == 0` ALONE, and then returned. So a report carrying a FIRED
        fault printed a sentence saying the world behaved, and never showed the
        fault at all: an absence asserted over evidence sitting in the same dict.
        """
        run_id = _store_run_whose_plan_was_emptied(reg)
        out = plain(run_cli(cfg, "scenario", "transcript", run_id).output)

        assert "the world was left to behave" not in out
        assert "disagrees with itself" in out
        assert "FIRED" in out                 # the evidence is still shown
        assert "0 staged" not in out

    def test_a_recorded_plan_of_nothing_does_not_read_as_nothing_fired(
            self, cfg, reg, no_network):
        """`0/0 fired` implies something was staged and did not happen. A run
        whose report is recorded and staged nothing is a different claim, and it
        is also not the "no report" state above."""
        scenario_run(cfg, "--seed", "3", "--intent", "refund",
                     "--tool-condition", "all_ok")
        listed = _flat(run_cli(cfg, "scenario", "list").output)
        assert "no fault staged" in listed
        assert "0/0 fired" not in listed
        assert "fault report not recorded" not in listed


def _flat(output: str) -> str:
    """One line, so an assertion about COPY is not an assertion about where rich
    happened to wrap the cell. The table rules go too — a phrase broken across
    two rows of the box is still the phrase the operator reads."""
    return re.sub(r"\s+", " ", plain(output).replace("│", " "))


def _store_run_with_an_unreadable_fault_report(reg) -> str:
    """One real run whose stored plan names a tool this world does not have.

    The case ``_faults_view`` documents: reconstruction VALIDATES, so the plan
    will not rebuild, ``never_reached`` and ``counts`` come back ``None`` and a
    ``problem`` appears beside the stored lists. Reached by rewriting the stored
    payload, which is not a supported operation and is the point — a row that
    has been rewritten is exactly the row a reader must not be lied to about.
    """
    import json

    from sqlmodel import Session as DBSession
    from sqlmodel import select

    from agenttic.registry.sqlite_store import ScenarioRunRow
    from agenttic.scenario.runner import (
        ScenarioAgent, ScriptedSupportClient, scenario_runner)
    from agenttic.scenario.tools import RETAIL_POLICY
    from agenttic.stimulus.realize import realize
    from agenttic.stimulus.space import sample_point
    from agenttic.stimulus.spaces.conversational_transactional import seed_space

    space = seed_space()
    point = sample_point(space, 3, pinned={"intent": "refund",
                                           "tool_condition": "timeout"})
    scn = realize(point, 3, space, policy=RETAIL_POLICY)
    agent = ScenarioAgent(model="scripted-support", client=ScriptedSupportClient(),
                          agent_id="unreadable-bot")
    outcome = scenario_runner()(scn, adapter=agent, store=reg)
    run_id = reg.save_scenario_run(scn, outcome)
    with DBSession(reg.engine) as s:
        row = s.exec(select(ScenarioRunRow).where(
            ScenarioRunRow.run_id == run_id)).one()
        data = json.loads(row.payload)
        assert data["fault_report"]["planned"], "this point stages no fault"
        data["fault_report"]["planned"][0]["tool"] = "search_knowledge_base"
        row.payload = json.dumps(data)
        s.add(row)
        s.commit()
    stored = reg.get_scenario_run(run_id)["faults"]
    assert stored["recorded"] is True and stored["counts"] is None
    return run_id


def _store_run_whose_plan_was_emptied(reg) -> str:
    """One real run that FIRED a fault, with the stored plan emptied underneath.

    Same rewrite route as the helper above and for the same reason: a row that
    has been rewritten is exactly the row a reader must not be lied to about.
    The resulting report says `planned: []` while `fired` still names the fault
    that happened — the two disagree, and no surface may resolve that by picking
    the reassuring half.
    """
    import json

    from sqlmodel import Session as DBSession
    from sqlmodel import select

    from agenttic.registry.sqlite_store import ScenarioRunRow
    from agenttic.scenario.runner import (
        ScenarioAgent, ScriptedSupportClient, scenario_runner)
    from agenttic.scenario.tools import RETAIL_POLICY
    from agenttic.stimulus.realize import realize
    from agenttic.stimulus.space import sample_point
    from agenttic.stimulus.spaces.conversational_transactional import seed_space

    space = seed_space()
    point = sample_point(space, 3, pinned={"intent": "refund",
                                           "tool_condition": "timeout"})
    scn = realize(point, 3, space, policy=RETAIL_POLICY)
    agent = ScenarioAgent(model="scripted-support", client=ScriptedSupportClient(),
                          agent_id="emptied-plan-bot")
    outcome = scenario_runner()(scn, adapter=agent, store=reg)
    run_id = reg.save_scenario_run(scn, outcome)
    with DBSession(reg.engine) as s:
        row = s.exec(select(ScenarioRunRow).where(
            ScenarioRunRow.run_id == run_id)).one()
        data = json.loads(row.payload)
        assert data["fault_report"]["fired"], "this point fired no fault"
        data["fault_report"]["planned"] = []
        row.payload = json.dumps(data)
        s.add(row)
        s.commit()
    return run_id


def _store_run_without_a_fault_report(reg) -> str:
    """One real run, filed with ``fault_report={}`` — an outcome somebody built
    by hand, which is the shape the store reads back as "not recorded"."""
    from agenttic.scenario.runner import (
        ScenarioAgent, ScriptedSupportClient, scenario_runner)
    from agenttic.scenario.tools import RETAIL_POLICY
    from agenttic.stimulus.realize import realize
    from agenttic.stimulus.space import sample_point
    from agenttic.stimulus.spaces.conversational_transactional import seed_space

    space = seed_space()
    point = sample_point(space, 11, pinned={"intent": "status"})
    scn = realize(point, 11, space, policy=RETAIL_POLICY)
    agent = ScenarioAgent(model="scripted-support", client=ScriptedSupportClient(),
                          agent_id="handmade")
    outcome = scenario_runner()(scn, adapter=agent, store=reg)
    return reg.save_scenario_run(scn, dataclasses.replace(outcome,
                                                          fault_report={}))


# --------------------------------------------------------------------------- #
# the world, changed and unchanged
# --------------------------------------------------------------------------- #


class TestTheStateDiff:
    def test_an_unchanged_world_reads_as_a_sentence_not_an_empty_section(
            self, cfg, no_network):
        out = scenario_run(cfg, *NEVER_REACHED)
        assert "the world was not changed" in out

    def test_a_changed_world_names_the_fields_that_moved(self, cfg, no_network):
        out = scenario_run(cfg, *FIRED)
        assert "refunded_usd" in out
        assert "the world was not changed" not in out

    def test_a_run_that_refused_nothing_says_refused_nothing(self, cfg,
                                                             no_network):
        assert "no call was refused" in scenario_run(cfg, *NEVER_REACHED)


# --------------------------------------------------------------------------- #
# coverage — credited from the trace, never from the request
# --------------------------------------------------------------------------- #


class TestCoverageIsCreditedFromWhatRan:
    def test_the_bins_the_run_exhibited_are_named(self, cfg, no_network):
        out = scenario_run(cfg, *NEVER_REACHED)
        assert "coverage exhibited" in out
        assert "trajectory:escalated_to_human" in out

    def test_a_requested_bin_the_run_never_produced_is_not_credited(
            self, cfg, no_network):
        """The point asked for `tool_condition=timeout` and the agent never made
        the call, so the bin was never exhibited. Crediting it from the request
        is the exact defect the two-number split exists to prevent."""
        out = scenario_run(cfg, *NEVER_REACHED)
        assert "tool_condition:timeout" not in out
        assert "asked for, never exhibited" in out
        assert "tool_condition=timeout" in out

    def test_the_stored_run_reports_the_same_bins(self, cfg, reg, no_network):
        scenario_run(cfg, *NEVER_REACHED)
        run_id = stored_run_id(reg)
        out = plain(run_cli(cfg, "scenario", "transcript", run_id).output)
        assert "trajectory:escalated_to_human" in out

    def test_an_unmodelled_bin_is_marked_and_a_fragment_is_not(self, cfg, reg,
                                                               no_network):
        """The `other` bin sits OUTSIDE closure, so it is annotated — and the
        annotation is decided on the whole token after the last colon.

        The negative half is the point: `trajectory:another` is not the `other`
        bin, and the substring test that would call it one is the dominant defect
        family in this repo's history ("resolve" in a read-verb list, "log"
        inside "dialog").
        """
        run_id = _store_run_with_bins(reg, ["trajectory:other",
                                            "trajectory:another"])
        out = plain(run_cli(cfg, "scenario", "transcript", run_id).output)
        marked = [ln for ln in out.splitlines() if "unmodelled bin" in ln]
        assert len(marked) == 1, out
        assert "trajectory:other" in marked[0]
        assert "trajectory:another" not in marked[0]

    def test_uncollected_coverage_is_not_coverage_that_credited_nothing(
            self, cfg, reg, no_network):
        run_id = _store_run_with_bins(reg, None)
        out = plain(run_cli(cfg, "scenario", "transcript", run_id).output)
        assert "no coverage was collected" in out

    def test_the_stored_bins_say_whose_vocabulary_they_are_in(
            self, cfg, reg, no_network):
        """`exhibited_bins` and `divergence` are DERIVED — a function of the
        trace AND of a coverage model — and the store cannot recompute them
        (it has no model and no collector). What it can do is refuse to let them
        be uninterpretable: `trajectory:tool_then_answer` means nothing without
        the model that names that bin, and comparing bin lists across a model
        version is the goalpost move `bins_fingerprint` exists to catch.
        """
        from agenttic.config import load_config
        from agenttic.coverage.models.baseline import baseline_model

        scenario_run(cfg, *FIRED)
        stored = reg.get_scenario_run(stored_run_id(reg))["coverage"]
        assert stored["bins"], "no bins stored — the test would be vacuous"

        model = baseline_model(cfg=load_config(str(cfg)))
        assert stored["model"]["ref"] == model.ref()
        assert stored["model"]["bins_fingerprint"] == model.bins_fingerprint()

    def test_a_not_measurable_dimension_is_never_credited_as_exhibited(
            self, cfg, reg, no_network):
        """A bin from a coverpoint the model declares NOT MEASURABLE must not be
        stored or printed as coverage the run exhibited.

        `session_shape` is `measurable=False` — "a trace with no turn markers is
        evidence of absent instrumentation, not of a single-turn session" — and
        the `single_turn` extractor is `_human_turns(trace) <= 1`, which is True
        at ZERO. A single-shot run emits no `user_turn` span at all, so crediting
        it turns missing instrumentation into a measured result: the vacuity rule
        inverted, on the one list headed "credited from the trace".

        The report agrees with this test and always did — `trace_closure` holds
        the dimension out. It was the CLI's own bin filter that routed around it.
        """
        scenario_run(cfg, "--seed", "7")
        run_id = stored_run_id(reg)
        stored = reg.get_scenario_run(run_id)
        bins = stored["coverage"]["bins"]

        turns = [s for s in reg.get_trace(stored["trace_id"]).spans
                 if "user_turn" in str(getattr(s, "kind", ""))]
        assert turns == []                      # nothing to measure a shape from
        assert bins, "the run exhibited nothing at all — test is vacuous"
        assert not any(b.startswith("session_shape:") for b in bins), bins

        out = plain(run_cli(cfg, "scenario", "transcript", run_id).output)
        assert "session_shape:single_turn" not in out


def _store_run_with_bins(reg, bins) -> str:
    """One real run filed with the caller's ``exhibited_bins`` — including
    ``None``, which is the "nobody measured" state and not an empty list."""
    from agenttic.scenario.runner import (
        ScenarioAgent, ScriptedSupportClient, scenario_runner)
    from agenttic.scenario.tools import RETAIL_POLICY
    from agenttic.stimulus.realize import realize
    from agenttic.stimulus.space import sample_point
    from agenttic.stimulus.spaces.conversational_transactional import seed_space

    space = seed_space()
    point = sample_point(space, 19, pinned={"intent": "status"})
    scn = realize(point, 19, space, policy=RETAIL_POLICY)
    agent = ScenarioAgent(model="scripted-support", client=ScriptedSupportClient(),
                          agent_id="handmade")
    outcome = scenario_runner()(scn, adapter=agent, store=reg)
    return reg.save_scenario_run(scn, outcome, exhibited_bins=bins)


# --------------------------------------------------------------------------- #
# the conversation
# --------------------------------------------------------------------------- #


class TestTheConversation:
    def test_a_single_shot_run_says_there_was_no_conversation(self, cfg,
                                                              no_network):
        out = scenario_run(cfg, *NEVER_REACHED)
        assert "single-shot ticket" in out

    def test_multi_turn_drives_the_counterparty_and_reports_how_it_ended(
            self, cfg, no_network):
        out = scenario_run(cfg, "--multi-turn", "--seed", "3",
                           "--intent", "account_change")
        assert "ended satisfied" in out
        assert "elicited: order_id" in out             # it had to ask
        assert "single-shot ticket" not in out

    def test_the_transcript_reads_turn_by_turn_with_what_each_turn_disclosed(
            self, cfg, reg, no_network):
        scenario_run(cfg, "--multi-turn", "--seed", "3",
                     "--intent", "account_change")
        out = plain(run_cli(cfg, "scenario", "transcript",
                            stored_run_id(reg)).output)
        assert "customer (open)" in out
        assert "customer (reveal)" in out
        assert "agent:" in out
        assert "discloses order_id" in out

    def test_the_closing_turn_is_marked_as_never_handed_to_the_agent(
            self, cfg, reg, no_network):
        """"Thanks, that's sorted" is said AFTER the agent's last answer. Drawing
        it as a message the agent ignored would describe a turn that never
        reached it."""
        scenario_run(cfg, "--multi-turn", "--seed", "3",
                     "--intent", "account_change")
        out = plain(run_cli(cfg, "scenario", "transcript",
                            stored_run_id(reg)).output)
        assert "never handed to the agent" in out

    def test_a_single_shot_transcript_says_so_instead_of_printing_nothing(
            self, cfg, reg, no_network):
        scenario_run(cfg, *NEVER_REACHED)
        out = plain(run_cli(cfg, "scenario", "transcript",
                            stored_run_id(reg)).output)
        assert "not a conversation" in out


class TestTheRunPrintsTheConversationItHeld:
    """`scenario run --multi-turn` is the one command that HOLDS a conversation,
    and it was the one command that never showed it — it drove the counterparty,
    stored every turn, and printed the tool calls, the faults, the world diff and
    how it ended without printing a word anybody said."""

    def test_a_multi_turn_run_prints_the_turns_it_just_stored(self, cfg,
                                                               no_network):
        out = scenario_run(cfg, "--multi-turn", "--seed", "3",
                           "--intent", "account_change")
        assert "customer (open)" in out
        assert "customer (reveal)" in out
        assert "agent:" in out
        assert "discloses order_id" in out

    def test_it_prints_what_the_reader_gets_back_later(self, cfg, reg,
                                                        no_network):
        """One renderer, both commands: the turns the run prints are the turns
        the stored row hands back."""
        first = scenario_run(cfg, "--multi-turn", "--seed", "3",
                             "--intent", "account_change")
        back = plain(run_cli(cfg, "scenario", "transcript",
                             stored_run_id(reg)).output)
        for turn in reg.get_scenario_run(stored_run_id(reg))["transcript"]:
            text = turn["text"][:40]
            assert text in _flat(first), text
            assert text in _flat(back), text

    def test_a_single_shot_run_says_there_were_no_turns(self, cfg, no_network):
        assert "not a conversation" in scenario_run(cfg, *NEVER_REACHED)


class TestElicitationIsNotVacuouslyComplete:
    """THREE states, and green is only ever the middle one.

    ``SimulatedSession.completed`` is *satisfied AND nothing still withheld*, so
    a run that gated NO fact is complete by construction — and the CLI printed
    that green. An unexercised check rendered as a good result is the M40 vacuity
    rule inverted, and it is the common case rather than a corner: ``realize()``
    interpolates the order id into most tickets, ``scenario/user.py`` therefore
    excludes it from the gate (``already_in_prompt``), and most intents gate on
    nothing at all.
    """

    #: nothing is gated: the order id is in the ticket, so the counterparty
    #: withholds nothing and the stand-in never has to ask.
    NOTHING_GATED = ("--multi-turn", "--seed", "1", "--intent", "refund")
    #: `account_change` carries no order number, so `order_id` really is gated —
    #: and the stand-in asks for it.
    GATED_AND_ELICITED = ("--multi-turn", "--seed", "3",
                          "--intent", "account_change")
    #: the same gated scenario, stopped at our own ceiling before the agent got
    #: to ask: the fact is still withheld.
    GATED_AND_WITHHELD = ("--multi-turn", "--max-turns", "1", "--seed", "3",
                          "--intent", "account_change")

    def test_a_run_that_gated_nothing_is_not_reported_complete(self, cfg,
                                                                no_network):
        out = _flat(scenario_run(cfg, *self.NOTHING_GATED))
        assert "elicited: nothing" in out            # the run really gated none
        assert "still withheld: nothing" in out
        assert "elicitation NOT EXERCISED" in out
        assert "the check never ran" in out
        assert "elicitation complete" not in out

    def test_a_gated_run_that_elicited_everything_is_complete(self, cfg,
                                                              no_network):
        out = _flat(scenario_run(cfg, *self.GATED_AND_ELICITED))
        assert "elicited: order_id" in out
        assert "elicitation complete" in out
        assert "NOT EXERCISED" not in out

    def test_a_gated_run_that_never_asked_is_incomplete(self, cfg, no_network):
        out = _flat(scenario_run(cfg, *self.GATED_AND_WITHHELD))
        assert "still withheld: order_id" in out
        assert "elicitation incomplete" in out
        assert "never asked for" in out
        assert "NOT EXERCISED" not in out

    def test_the_three_states_do_not_share_a_line(self, cfg, tmp_path,
                                                   no_network):
        """The acceptance criterion is that they LOOK different, so read the
        verdict line off each of the three and require three distinct lines."""
        def verdict(args) -> str:
            lines = [ln.strip() for ln in scenario_run(cfg, *args).splitlines()
                     if "elicitation" in ln.lower()]
            assert lines, "no elicitation verdict was printed at all"
            return " ".join(lines)

        said = {verdict(self.NOTHING_GATED),
                verdict(self.GATED_AND_ELICITED),
                verdict(self.GATED_AND_WITHHELD)}
        assert len(said) == 3, said

    def test_the_stored_run_reads_back_the_same_way(self, cfg, reg,
                                                    no_network):
        scenario_run(cfg, *self.NOTHING_GATED)
        out = _flat(run_cli(cfg, "scenario", "transcript",
                            stored_run_id(reg)).output)
        assert "elicitation NOT EXERCISED" in out
        assert "elicitation complete" not in out


class TestDivergenceOutlivesTheProcess:
    """`asked for, never exhibited` was printed from the live report and never
    stored — so the one finding this product exists to make could not be read
    back out of the row that claimed to hold the run, and the command that stored
    it was showing a fact the store had dropped."""

    def test_the_divergence_the_run_printed_is_in_the_row_it_stored(
            self, cfg, reg, no_network):
        out = scenario_run(cfg, *NEVER_REACHED)
        assert "asked for, never exhibited" in out
        assert "tool_condition=timeout" in out

        rows = reg.get_scenario_run(stored_run_id(reg))["coverage"]["divergence"]
        assert rows is not None, "printed live and stored as NOT RECORDED"
        assert {(r["coverpoint_id"], r["bin_id"]) for r in rows} == {
            ("tool_condition", "timeout")}

    def test_the_stored_run_prints_the_same_divergence(self, cfg, reg,
                                                       no_network):
        scenario_run(cfg, *NEVER_REACHED)
        out = _flat(run_cli(cfg, "scenario", "transcript",
                            stored_run_id(reg)).output)
        assert "asked for, never exhibited: tool_condition=timeout" in out

    def test_nothing_diverged_is_a_measurement_not_an_absence(self, cfg, reg,
                                                              no_network):
        """``[]`` is a computation that found nothing, and it must not read as
        the run nobody computed it for."""
        out = _flat(scenario_run(cfg, "--multi-turn", "--seed", "2",
                                 "--intent", "refund"))
        assert reg.get_scenario_run(stored_run_id(reg))["coverage"][
            "divergence"] == []
        assert "nothing diverged" in out
        assert "was not computed" not in out
        assert "asked for, never exhibited" not in out

    def test_a_row_nobody_computed_it_for_says_so(self, cfg, reg, no_network):
        """``None`` — the state a row written before the field existed carries.
        It is not a finding of none, and it is not an empty list."""
        run_id = _store_run_with_bins(reg, ["trajectory:other"])
        assert reg.get_scenario_run(run_id)["coverage"]["divergence"] is None
        out = _flat(run_cli(cfg, "scenario", "transcript", run_id).output)
        assert "divergence was not computed for this run" in out
        assert "nothing diverged" not in out

    def test_it_is_never_summed_into_the_coverage_figure(self, cfg, reg,
                                                         no_network):
        """A fact about the GENERATOR's reach, never about the agent — so the
        requested-but-unexhibited bin is not in the exhibited list."""
        out = scenario_run(cfg, *NEVER_REACHED)
        exhibited = out.split("coverage exhibited")[1].split("divergence")[0]
        assert "tool_condition:timeout" not in exhibited


def _render_divergence_of(row: dict) -> str:
    """The divergence block for one hand-built row, through the SHIPPED renderer.

    A unit test rather than another CLI invocation because the states below —
    a point every corner of which was compared, and a row carrying no point at
    all — are not states the default path can produce, and a criterion that can
    only be demonstrated on the one path that happens to violate it is not
    pinned at all.
    """
    from agenttic.cli import _render_divergence, console
    with console.capture() as cap:
        _render_divergence(row)
    return _flat(cap.get())


class TestDivergenceNeverClosesOverCornersNothingCompared:
    """``[]`` said *every corner the point requested was exhibited by the run* —
    a UNIVERSAL claim over a set that had been quietly reduced.

    ``collect()`` records a stimulus hit only for a requested dimension the model
    names and has that bin for (``if want and want in cov.bins``), and
    ``divergence()`` further skips a coverpoint that is not measurable. On the
    default path the point carries five dimensions and ``baseline_model`` names
    two of them — its own ``BASELINE_LIMITS`` says it "does NOT cover intent,
    emotional register or policy pressure" — so three of the five corners were
    compared against nothing and the sentence spoke for all five anyway. Empty
    set implies success: the one shape this product exists to refuse, on the one
    sentence it exists to say.
    """

    def test_the_default_path_names_every_corner_nothing_compared(
            self, cfg, reg, no_network):
        from agenttic.coverage.models.baseline import baseline_model

        out = _flat(scenario_run(cfg, "--seed", "1"))
        run = reg.get_scenario_run(stored_run_id(reg))
        point = run["point"]
        assert run["coverage"]["divergence"] == [], "not the `[]` state"

        model = baseline_model()
        unnamed = {k: v for k, v in point.items() if model.coverpoint(k) is None}
        assert unnamed, ("the baseline model names every requested dimension — "
                         "this test would be vacuous")

        assert "every corner the point requested was exhibited" not in out
        for cp_id, bin_id in unnamed.items():
            assert f"never compared: {cp_id}={bin_id}" in out, out

    def test_the_two_counts_are_arithmetic_over_the_point_and_not_decoration(
            self, cfg, reg, no_network):
        """`n of m` is checkable: m is the size of the stored point, and the
        corners named as never compared are exactly the rest of it."""
        out = _flat(scenario_run(cfg, "--seed", "1"))
        point = reg.get_scenario_run(stored_run_id(reg))["point"]

        compared = re.search(r"nothing diverged among the (\d+) of (\d+) "
                             r"corners", out)
        never = re.search(r"(\d+) of the (\d+) corners the point requested were "
                          r"never compared", out)
        assert compared and never, out
        assert int(compared.group(2)) == int(never.group(2)) == len(point)
        assert int(compared.group(1)) + int(never.group(1)) == len(point)

    def test_the_stored_run_reads_back_the_same_way(self, cfg, reg, no_network):
        """Both commands go through the one renderer, so a corner nothing
        compared cannot be a thing only the storing command mentions."""
        scenario_run(cfg, "--seed", "1")
        out = _flat(run_cli(cfg, "scenario", "transcript",
                            stored_run_id(reg)).output)
        assert "were never compared to anything" in out
        assert "every corner the point requested was exhibited" not in out

    def test_a_divergence_that_found_something_says_it_too(self, cfg, reg,
                                                           no_network):
        """`[rows]` is just as silent about the corners nothing compared, and
        "these diverged" reads as "and the rest were fine"."""
        out = _flat(scenario_run(cfg, *NEVER_REACHED))
        assert "asked for, never exhibited: tool_condition=timeout" in out
        assert "were never compared to anything" in out

    def test_a_point_every_corner_of_which_was_compared_still_says_every(self):
        """The fix is a distinction, not a blanket hedge: a run whose every
        requested corner reached the comparison must still say so, or the two
        states have merely swapped which one is unsayable."""
        out = _render_divergence_of({
            "point": {"tool_condition": "timeout", "data_condition": "complete"},
            "coverage": {"measured": True, "divergence": [],
                         "bins": ["tool_condition:timeout",
                                  "data_condition:complete"]}})
        assert "all 2 corners the point requested were exhibited" in out
        assert "never compared" not in out

    def test_the_two_states_do_not_print_the_same_sentence(self):
        """The acceptance criterion, stated directly."""
        whole = _render_divergence_of({
            "point": {"tool_condition": "timeout"},
            "coverage": {"measured": True, "divergence": [],
                         "bins": ["tool_condition:timeout"]}})
        reduced = _render_divergence_of({
            "point": {"tool_condition": "timeout", "intent": "complaint"},
            "coverage": {"measured": True, "divergence": [],
                         "bins": ["tool_condition:timeout"]}})
        assert whole != reduced
        assert "never compared: intent=complaint" in reduced
        assert "never compared" not in whole

    def test_a_corner_named_by_the_divergence_list_counts_as_compared(self):
        """The comparison has two halves and a corner reported as DIVERGED was
        very much compared — reading only the exhibited bins would report the
        divergence rows themselves as uncompared."""
        out = _render_divergence_of({
            "point": {"tool_condition": "timeout"},
            "coverage": {"measured": True, "bins": [],
                         "divergence": [{"coverpoint_id": "tool_condition",
                                         "bin_id": "timeout",
                                         "requested": 1, "exhibited": 0}]}})
        assert "asked for, never exhibited: tool_condition=timeout" in out
        assert "never compared" not in out

    def test_a_row_with_no_point_does_not_claim_every_corner_appeared(self):
        """`[]` over a row that records no point is the same defect one step
        out: a universal claim over the empty set."""
        out = _render_divergence_of({
            "point": {}, "coverage": {"measured": True, "divergence": [],
                                      "bins": []}})
        assert "records no stimulus point" in out
        assert "every corner" not in out

    def test_nobody_computed_it_still_outranks_the_corner_count(self):
        """`None` is unchanged: nothing was compared, and listing corners under
        it would dress an absent computation as a partial one."""
        out = _render_divergence_of({
            "point": {"intent": "complaint"},
            "coverage": {"measured": True, "divergence": None, "bins": []}})
        assert "divergence was not computed for this run" in out
        assert "never compared" not in out
        assert "nothing diverged" not in out


class TestTwoDifferentThingsDoNotPrintIdentically:
    def test_a_truncated_hash_is_marked_and_a_whole_one_is_not(
            self, cfg, reg, no_network):
        """``space_fingerprint`` IS 16 characters (``sha256()[:16]``) — a whole
        value. ``content_sha256`` is 64 and was cut to 16 with no marker, right
        beside it: two identical-looking tokens, one of them a quarter of
        itself, with nothing on the line saying which."""
        scenario_run(cfg, *NEVER_REACHED)
        run = reg.get_scenario_run(stored_run_id(reg))
        sha, fp = run["derived"]["content_sha256"], run["space_fingerprint"]
        assert len(sha) == 64 and len(fp) == 16, (sha, fp)

        out = _flat(run_cli(cfg, "scenario", "transcript",
                            run["run_id"]).output)
        assert sha not in out                        # not printed whole
        assert f"content {sha[:16]}…" in out         # cut, and SAID to be cut
        assert f"fingerprint {fp} ·" in out          # whole, and unmarked

    def test_a_stored_timestamp_names_its_zone(self, cfg, reg, no_network):
        """The store writes UTC and SQLite hands it back naive, so the string
        carries no offset and a reader has no way to tell it is not local."""
        scenario_run(cfg, *NEVER_REACHED)
        detail = _flat(run_cli(cfg, "scenario", "transcript",
                               stored_run_id(reg)).output)
        assert re.search(r"run at \S+ UTC", detail), detail
        assert "UTC" in _flat(run_cli(cfg, "scenario", "list").output)


class TestTheListSaysWhatItIsNotShowing:
    """A capped list that reads as a complete one is a false claim about how
    much evidence exists — and it is the claim an operator acts on."""

    def test_a_capped_list_says_it_was_capped(self, cfg, no_network):
        scenario_run(cfg, *NEVER_REACHED)
        scenario_run(cfg, *FIRED)
        out = _flat(run_cli(cfg, "scenario", "list", "--limit", "1").output)
        assert "1 run(s) shown" in out
        assert "CAPPED at --limit 1" in out
        assert "older runs it does not show" in out
        assert "complete list" not in out

    def test_an_uncapped_list_says_it_is_the_whole_list(self, cfg, no_network):
        scenario_run(cfg, *NEVER_REACHED)
        scenario_run(cfg, *FIRED)
        out = _flat(run_cli(cfg, "scenario", "list").output)
        assert "2 run(s) shown" in out
        assert "complete list for this filter" in out
        assert "CAPPED" not in out

    def test_a_filtered_zero_is_not_an_empty_store(self, cfg, no_network):
        """Found beside the capped-list defect and the same family: zero rows
        UNDER A FILTER is a measurement, and printing "No scenario runs stored"
        for it is a false claim about the store — in the one place a reader goes
        to find out how much evidence exists."""
        scenario_run(cfg, *NEVER_REACHED, "--agent", "present-bot")
        out = _flat(run_cli(cfg, "scenario", "list",
                            "--agent", "absent-bot").output)
        assert "No scenario runs stored" not in out
        assert "--agent absent-bot" in out
        assert "not the same claim as no run being stored" in out

    def test_a_truly_empty_store_still_says_so(self, cfg):
        out = _flat(run_cli(cfg, "scenario", "list").output)
        assert "No scenario runs stored" in out

    def test_the_page_size_is_still_the_page_size(self, cfg, reg, no_network):
        """The extra row is fetched to answer "is there more" and DROPPED — a
        `--limit 1` that printed two rows would be a different lie."""
        scenario_run(cfg, *NEVER_REACHED)
        scenario_run(cfg, *FIRED)
        out = plain(run_cli(cfg, "scenario", "list", "--limit", "1").output)
        ids = [r["run_id"] for r in reg.list_scenario_runs()]
        assert len(ids) == 2
        assert sum(1 for i in ids if i in out) == 1


class TestSessionShapeIsHeldOutByAFlagAndNotByAGate:
    def test_no_shipped_model_declares_a_measurability_gate(self):
        """The fact ``extractors._single``'s docstring asserts, pinned so the
        prose cannot drift from the models again.

        It used to claim `session_shape` declares
        ``measurable_when="session_turns_instrumented"``. Nothing does — the
        registered gate has no reference — and what actually holds the dimension
        out is the per-model ``measurable=False`` flag. The difference matters:
        ``session_single_turn`` is ``<= 1``, which is True at ZERO turns, so on
        this build the predicate IS answered for uninstrumented traces and only
        the flag stands between that answer and a closure figure.
        """
        from agenttic.coverage.models.baseline import baseline_model
        from agenttic.coverage.models.conversational_transactional import (
            seed_model)

        cfg = {"coverage": {"closure_target": 0.95}}
        for model in (baseline_model(cfg=cfg), seed_model(cfg=cfg)):
            shape = next(c for c in model.coverpoints
                         if c.coverpoint_id == "session_shape")
            assert shape.measurable is False
            assert shape.measurable_when is None
            assert shape.not_measurable_reason.strip()
            # and no OTHER coverpoint quietly declares one either
            assert [c.coverpoint_id for c in model.coverpoints
                    if c.measurable_when] == []

    def test_the_predicate_is_true_at_zero_turns(self):
        """Which is why the flag is load-bearing rather than belt-and-braces."""
        from agenttic.coverage.extractors import run_predicate
        from agenttic.schema.trace import Trace

        bare = Trace(trace_id="t-empty", agent_id="a", agent_config_hash="h",
                     visibility="black_box", final_output="", spans=[])
        assert run_predicate("session_single_turn", bare) is True


# --------------------------------------------------------------------------- #
# failing cleanly
# --------------------------------------------------------------------------- #


class TestItFailsCleanly:
    def test_transcript_on_an_unknown_id_is_a_usage_error_not_a_traceback(
            self, cfg):
        r = run_cli(cfg, "scenario", "transcript", "no-such-run")
        assert r.exit_code != 0
        out = plain(r.output)
        assert "Traceback" not in out
        assert "no-such-run" in out
        assert "scenario list" in out                  # where to look instead

    def test_an_unknown_intent_is_a_usage_error(self, cfg):
        r = run_cli(cfg, "scenario", "run", "--intent", "teleportation")
        assert r.exit_code != 0
        out = plain(r.output)
        assert "Traceback" not in out
        assert "teleportation" in out

    def test_an_unknown_tool_condition_is_a_usage_error(self, cfg):
        r = run_cli(cfg, "scenario", "run", "--tool-condition", "gremlins")
        assert r.exit_code != 0
        assert "Traceback" not in plain(r.output)

    def test_an_unknown_space_points_at_the_derived_one(self, cfg):
        r = run_cli(cfg, "scenario", "run", "--space", "space-does-not-exist")
        assert r.exit_code != 0
        out = plain(r.output)
        assert "Traceback" not in out
        assert "surface save" in out


# --------------------------------------------------------------------------- #
# offline
# --------------------------------------------------------------------------- #


class TestItRunsOffline:
    def test_every_command_runs_with_no_api_key_and_no_socket(
            self, cfg, reg, no_key, no_network):
        """The acceptance criterion, end to end: run, list, transcript — no key,
        and a socket block that turns any network call into a failure."""
        assert "NEVER REACHED" in scenario_run(cfg, *NEVER_REACHED)
        run_id = stored_run_id(reg)
        for args in (("scenario", "list"),
                     ("scenario", "transcript", run_id)):
            r = run_cli(cfg, *args)
            assert r.exit_code == 0, r.output

    def test_the_same_seed_realizes_the_same_scenario(self, cfg, tmp_path,
                                                      no_key, no_network):
        """Reproducible from the seed plus the space version (Hard Rule 57) —
        the property every other claim here is stated against."""
        first = scenario_run(cfg, *NEVER_REACHED)
        second = scenario_run(cfg, *NEVER_REACHED)
        ids = re.findall(r"scenario (scn-[0-9a-f]{16})", first + second)
        assert len(ids) == 2 and ids[0] == ids[1]
