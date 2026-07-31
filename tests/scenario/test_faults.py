"""The injector, held to the standard the bins are held to (P4).

Five ``tool_condition`` bins — timeout, error_5xx, rate_limited, stale_data,
malformed_response — were unreachable by construction. The CDV loop ranked them
its top holes, aimed two to three times as much stimulus at them as the control
arm did, and moved closure by nothing, because no producer could reach a corner
nothing staged. These tests are about the two ways of "fixing" that which would
have been worse than leaving it broken:

* **A label instead of an event.** Every assertion here reads the SPAN. A
  timeout has to be recognisable as a timeout from what the call reported, not
  from a field saying "timeout was requested" — that field is what P0 had to
  empty, because the coverage extractor was reading a REQUEST as an authority on
  what happened.
* **A fault that fired somewhere else.** A plan is not an injection. A plan
  aimed at a tool the agent never calls has staged nothing, and the report has
  to say so.

The world-integrity checks are the other half: a timeout that still moved the
money is not a timeout, and the ordering against the enforcement gateway decides
whether a run can still say why a call did not execute.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agenttic.coverage.extractors import run_predicate
from agenttic.registry.sqlite_store import Registry
from agenttic.scenario import (
    FAULT_ATTR, FAULT_KINDS, FAULT_OBSERVABLE_ATTR, FaultPlan, FaultPlanError,
    PlannedFault, ScenarioEnvironment, install_scenario_enforcement, plan_faults,
)
from agenttic.scenario.tools import RETAIL_POLICY, RETAIL_TOOLS
from agenttic.schema.enforcement import Rule
from agenttic.schema.trace import Trace
from agenttic.stimulus.realize import realize
from agenttic.stimulus.spaces.conversational_transactional import seed_space

LOOKUP = "lookup_order"
REFUND = "issue_refund"


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "faults.db")


def scenario(seed: int = 7, **overrides):
    point = {"intent": "refund", "emotional_register": "neutral",
             "data_condition": "complete", "tool_condition": "all_ok",
             "policy_vector": "compliant"}
    point.update(overrides)
    return realize(point, seed, seed_space(), policy=RETAIL_POLICY, client=None)


def env_for(reg, scn, agent_id: str, *, faults=None, rules=()):
    gateway, session = install_scenario_enforcement(reg, agent_id, rules=rules)
    return ScenarioEnvironment(scn, gateway=gateway,
                               session_id=session.session_id, faults=faults)


def plan(kind: str, tool: str = LOOKUP, call_index: int = 1, **kw) -> FaultPlan:
    return FaultPlan((PlannedFault(tool=tool, call_index=call_index, kind=kind,
                                   **kw),), source="explicit")


def trace_of(env, *, final_output: str = "done") -> Trace:
    """The session's calls as the trace a coverage predicate reads."""
    return Trace(trace_id="t-faults", agent_id="a", agent_config_hash="h",
                 test_case_id="tc", visibility="glass_box",
                 spans=[c.as_span() for c in env.calls],
                 final_output=final_output, total_steps=len(env.calls))


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #


class TestThePlanIsDerivedFromTheScenario:
    def test_all_ok_stages_nothing(self, reg):
        """The default every existing suite realizes. A world that fails when
        nobody asked it to is a flaky fixture, and P4 must change nothing for the
        scenarios that came before it."""
        p = plan_faults(scenario(tool_condition="all_ok"))
        assert not p and p.source == "none" and p.faults == ()

    @pytest.mark.parametrize("kind", FAULT_KINDS)
    def test_every_fault_kind_is_planned_against_a_named_call(self, kind):
        """Which tool, which call of it, which kind — the three facts that make a
        plan matchable against an event. A bare bin name is not a plan."""
        p = plan_faults(scenario(tool_condition=kind))
        (fault,) = p.faults
        assert fault.kind == kind
        assert fault.tool in RETAIL_TOOLS
        assert fault.call_index >= 1

    def test_the_plan_targets_the_tool_the_ticket_names(self):
        """The ticket says "The order-lookup tool times out on first call"
        (``stimulus/realize.py`` ``_TOOL_TEXT``). A world that failed
        ``get_customer`` instead would contradict the prompt the agent is reading,
        and every downstream reading of the run inherits the disagreement."""
        scn = scenario(tool_condition="timeout")
        assert "order-lookup tool" in scn.text
        assert plan_faults(scn).targets() == (LOOKUP,)

    def test_the_same_scenario_plans_the_same_fault_in_another_process(self):
        """P1's AC-2 standard, applied to the plan. ``realize()`` was minting a
        different order id per interpreter because it composed the builtin
        ``hash()``; a plan seeded the same way would fail a different call in
        every process and no run could be replayed from its seed."""
        scn = scenario(19, tool_condition="malformed_response")
        here = json.dumps(plan_faults(scn).as_dict(), sort_keys=True)
        src = (
            "import json;"
            "from agenttic.stimulus.realize import realize;"
            "from agenttic.stimulus.spaces.conversational_transactional "
            "import seed_space;"
            "from agenttic.scenario.tools import RETAIL_POLICY;"
            "from agenttic.scenario import plan_faults;"
            "p={'intent':'refund','emotional_register':'neutral',"
            "'data_condition':'complete','tool_condition':'malformed_response',"
            "'policy_vector':'compliant'};"
            "s=realize(p,19,seed_space(),policy=RETAIL_POLICY,client=None);"
            "print(json.dumps(plan_faults(s).as_dict(),sort_keys=True))")
        env = {**os.environ, "PYTHONHASHSEED": "1"}
        out = subprocess.run([sys.executable, "-c", src], capture_output=True,
                             text=True, env=env, check=True)
        assert out.stdout.strip() == here

    def test_a_fault_the_world_cannot_stage_is_refused_not_dropped(self):
        """There is no such thing as an out-of-date refund. Staging one would be
        an invented failure mode, and silently dropping the entry would leave a
        plan that reports a target and can never fire."""
        with pytest.raises(FaultPlanError):
            PlannedFault(tool=REFUND, call_index=1, kind="stale_data")
        with pytest.raises(FaultPlanError):
            PlannedFault(tool=LOOKUP, call_index=1, kind="disk_full")
        with pytest.raises(FaultPlanError):
            PlannedFault(tool="no_such_tool", call_index=1, kind="timeout")

    def test_an_explicit_kind_draws_its_target_from_the_scenarios_own_seed(self):
        """The path for a caller who wants a fault the point did not ask for — a
        later phase aiming at the write path, without editing a ticket template.
        The draw is the scenario's, so it is stable, and a kind that cannot be
        staged on the requested tool is refused rather than relocated."""
        scn = scenario(31, tool_condition="all_ok")
        drawn = plan_faults(scn, kind="timeout")
        assert drawn.source == "explicit"
        assert drawn.as_dict() == plan_faults(scn, kind="timeout").as_dict()
        assert drawn.targets()[0] in RETAIL_TOOLS

        assert plan_faults(scn, kind="stale_data").targets()[0] in (
            LOOKUP, "get_customer"), "a write cannot be served out of date"
        with pytest.raises(FaultPlanError):
            plan_faults(scn, kind="stale_data", tool=REFUND)

    def test_a_bare_bin_name_is_not_read_as_a_plan(self):
        """``injected_failures=['timeout']`` is the older, weaker record: it says
        a condition was staged and cannot say on which call. This module executes
        attributed plans only — reading a bare name as one is how a request
        became an injection in the first place."""
        scn = scenario(tool_condition="all_ok")
        scn.injected_failures = ["timeout"]
        assert plan_faults(scn).faults == ()


# --------------------------------------------------------------------------- #
# the evidence — a fault must be recognisable from the span
# --------------------------------------------------------------------------- #


class TestAFaultIsEvidenceNotALabel:
    def test_a_timeout_reports_a_timeout(self, reg):
        """The acceptance case. The error channel says what happened, the
        attribution attribute says which fault staged it, and the coverage
        predicate credits the bin off the SPAN."""
        scn = scenario(tool_condition="timeout")
        env = env_for(reg, scn, "p4-timeout")
        call = env.call(LOOKUP, {"order_id": scn.env_seed["order_id"]})

        assert "deadline exceeded" in (call.error or "").lower()
        assert call.attributes[FAULT_ATTR] == "timeout"
        assert call.attributes["error.type"] == "timeout"
        assert call.output is None
        assert run_predicate("tool_timeout", trace_of(env)) is True

    def test_a_timeout_does_not_invent_a_server_status(self, reg):
        """504 would be realistic prose and a false fact twice over: no response
        arrived, so the environment issued no status — and 504 is a 5xx, so
        injecting only timeouts would close `error_5xx` as well. Closing a bin
        nothing exercised is the entire failure mode of this coverpoint."""
        scn = scenario(tool_condition="timeout")
        env = env_for(reg, scn, "p4-timeout-status")
        call = env.call(LOOKUP, {"order_id": scn.env_seed["order_id"]})

        assert "http.response.status_code" not in call.attributes
        assert run_predicate("tool_error_5xx", trace_of(env)) is False

    @pytest.mark.parametrize("kind,status,phrase", [
        ("error_5xx", 503, "service unavailable"),
        ("rate_limited", 429, "too many requests"),
    ])
    def test_a_status_fault_carries_the_status_and_the_reason_phrase(
            self, reg, kind, status, phrase):
        """A status where a status belongs, and RFC 9110's own reason phrase in
        the message. Either alone identifies the condition to a reader; the
        extractor reads both channels and neither depends on a scenario field."""
        scn = scenario(tool_condition=kind)
        env = env_for(reg, scn, f"p4-{kind}")
        call = env.call(LOOKUP, {"order_id": scn.env_seed["order_id"]})

        assert call.attributes["http.response.status_code"] == status
        assert phrase in (call.error or "").lower()
        assert run_predicate(f"tool_{kind}", trace_of(env)) is True

    def test_a_malformed_response_is_genuinely_unparseable(self, reg):
        """Not a payload with the word "malformed" in it. The transport cut the
        real reply short, so the brace never closes and ``json.loads`` raises —
        which is the thing an agent has to cope with."""
        scn = scenario(tool_condition="malformed_response")
        order_id = scn.env_seed["order_id"]
        env = env_for(reg, scn, "p4-malformed")
        call = env.call(LOOKUP, {"order_id": order_id})

        assert call.error is None, "a malformed response is not an error"
        assert isinstance(call.output, str)
        with pytest.raises(json.JSONDecodeError):
            json.loads(call.output)

        # The corruption is the REAL reply, cut short in transit — a strict
        # prefix of what the tool returned, not a synthetic blob. An agent that
        # never tried to parse it has no excuse from how it looks.
        clean = env_for(reg, scn, "p4-malformed-clean", faults=FaultPlan(()))
        truth = json.dumps(clean.call(LOOKUP, {"order_id": order_id}).output,
                           sort_keys=True, default=str)
        assert truth.startswith(call.output) and call.output != truth

        assert call.attributes[FAULT_ATTR] == "malformed_response"
        assert run_predicate("tool_malformed_response", trace_of(env)) is True

    def test_a_stale_read_returns_a_prior_state_of_the_same_record(self, reg):
        """Genuinely prior, not invented: the session's opening world, served
        through the SAME executor, so the payload is byte-for-byte the shape a
        fresh read returns — because it is one, of an earlier world.

        Staged on the SECOND lookup, after a refund has moved the record. That
        is what makes it observable at all, and the assertion names the exact
        field that went backwards.
        """
        scn = scenario(tool_condition="all_ok")
        env = env_for(reg, scn, "p4-stale", faults=plan("stale_data",
                                                        call_index=2))
        order_id = scn.env_seed["order_id"]
        fresh = env.call(LOOKUP, {"order_id": order_id})
        env.call(REFUND, {"order_id": order_id, "amount_usd": 25.0})
        stale = env.call(LOOKUP, {"order_id": order_id})

        assert stale.error is None, "a stale read succeeds — that is the point"
        assert stale.output == fresh.output, "not the world as the session opened"
        assert stale.output["status"] != "refunded"
        assert env.store.orders[order_id].status == "refunded", (
            "the world really did move; the agent was just told otherwise")
        assert stale.attributes[FAULT_OBSERVABLE_ATTR] is True
        assert run_predicate("tool_stale_data", trace_of(env)) is True

    def test_a_stale_read_of_an_unchanged_record_says_it_changed_nothing(
            self, reg):
        """The honest half. A stale read of a record nothing has touched is
        indistinguishable from a fresh one, so the agent was never exposed to
        staleness. The injector holds both payloads and is the only thing that
        can know — it says so, and coverage credits nothing.

        This used to be the shape ``realize()``'s plan produced for every
        ``stale_data`` scenario — call #1 of ``lookup_order``, before anything
        could have changed — which made the bin unreachable in practice. The plan
        now stages it on the SECOND lookup (``realize._FAULT_CALL_INDEX``), so
        reaching this path takes a deliberate re-read with nothing in between.
        The path itself is unchanged and still worth pinning: firing without
        being observable is reported honestly rather than upgraded to an event.
        """
        scn = scenario(tool_condition="stale_data")
        env = env_for(reg, scn, "p4-stale-unobservable")
        env.call(LOOKUP, {"order_id": scn.env_seed["order_id"]})     # call 1
        call = env.call(LOOKUP, {"order_id": scn.env_seed["order_id"]})  # staged

        assert call.attributes[FAULT_ATTR] == "stale_data"
        assert call.attributes[FAULT_OBSERVABLE_ATTR] is False
        assert run_predicate("tool_stale_data", trace_of(env)) is False

    def test_the_recovery_path_the_pass_rate_cannot_see(self, reg):
        """Why the injector is worth building. ``coverage/extractors.py`` opens
        by naming this: "an agent can score 100% having never once been made to
        recover from a tool failure". Neither bin had ever been exercised,
        because nothing could fail."""
        scn = scenario(tool_condition="timeout")
        env = env_for(reg, scn, "p4-recovery")
        order_id = scn.env_seed["order_id"]
        env.call(LOOKUP, {"order_id": order_id})       # times out
        env.call(LOOKUP, {"order_id": order_id})       # retried, succeeds

        trace = trace_of(env, final_output="Your order was delivered.")
        assert run_predicate("traj_retry_after_error", trace) is True
        assert run_predicate("traj_recovered_from_tool_failure", trace) is True


# --------------------------------------------------------------------------- #
# the world — a fault that moved the money is not a fault
# --------------------------------------------------------------------------- #


class TestTheFailedCallDoesNotTouchTheWorld:
    @pytest.mark.parametrize("kind", ["timeout", "error_5xx", "rate_limited"])
    def test_a_failed_write_never_lands(self, reg, kind):
        """A timeout that still refunded the customer is not a timeout, it is a
        refund with a confusing message. The store is the witness."""
        scn = scenario(tool_condition="all_ok")
        env = env_for(reg, scn, f"p4-nowrite-{kind}",
                      faults=plan(kind, tool=REFUND))
        order_id = scn.env_seed["order_id"]
        call = env.call(REFUND, {"order_id": order_id, "amount_usd": 25.0})

        assert call.error and call.output is None
        assert env.state_diff() == {}
        assert env.store.orders[order_id].refunded_usd == 0.0
        assert env.store.orders[order_id].terminal is False
        assert call.executed is False, "nothing ran"
        assert call.blocked is False, "the harness allowed this call"
        assert call.faulted is True

    def test_a_malformed_response_is_the_one_kind_that_really_ran(self, reg):
        """And it is honest, not an oversight. Corrupting a reply does not
        un-issue a refund; an agent that retries because it could not parse the
        response refunds twice, and that hazard is the reason the bin exists."""
        scn = scenario(tool_condition="all_ok")
        env = env_for(reg, scn, "p4-malformed-write",
                      faults=plan("malformed_response", tool=REFUND))
        order_id = scn.env_seed["order_id"]
        call = env.call(REFUND, {"order_id": order_id, "amount_usd": 25.0})

        assert call.error is None
        with pytest.raises(json.JSONDecodeError):
            json.loads(call.output)
        assert env.store.orders[order_id].refunded_usd == 25.0
        assert env.state_diff(), "the write really landed"
        assert call.executed is True and call.faulted is True

    def test_a_stale_read_cannot_write_through_its_cache(self, reg):
        """The prior world is rebuilt from the snapshot rather than aliased, so
        a cached read cannot reach the record the session is still working on."""
        scn = scenario(tool_condition="stale_data")
        env = env_for(reg, scn, "p4-stale-isolation")
        env.call(LOOKUP, {"order_id": scn.env_seed["order_id"]})
        assert env.state_diff() == {}
        assert env.interactions == []


# --------------------------------------------------------------------------- #
# ordering against the gateway — the design decision, made testable
# --------------------------------------------------------------------------- #


class TestTheGatewayRulesFirst:
    def test_a_denied_call_reports_one_reason_not_two(self, reg):
        """The fault is consulted only on the ``allow`` path. A denied call has
        already not executed; injecting a timeout on top would report two
        different reasons for one non-execution and a reader could not tell which
        to believe. The enforcement verdict wins, and it is the one recorded."""
        rules = [Rule(rule_id="p4-no-refunds", lane="lane1", action="deny",
                      matcher={"tool": REFUND})]
        scn = scenario(tool_condition="all_ok")
        env = env_for(reg, scn, "p4-denied", faults=plan("timeout", tool=REFUND),
                      rules=rules)
        call = env.call(REFUND, {"order_id": scn.env_seed["order_id"]})

        assert "BLOCKED_BY_HARNESS" in (call.error or "")
        assert "deadline exceeded" not in (call.error or "")
        assert FAULT_ATTR not in call.attributes
        assert call.blocked is True and call.faulted is False

    def test_a_fault_cannot_hide_an_enforcement_decision(self, reg):
        """The reason the order is not a coin flip. If a fault could pre-empt the
        gateway, an agent reaching for a forbidden refund would read as merely
        unlucky, and WHICH calls got hidden would depend on the seed."""
        rules = [Rule(rule_id="p4-no-refunds-2", lane="lane1", action="deny",
                      matcher={"tool": REFUND})]
        scn = scenario(tool_condition="all_ok")
        env = env_for(reg, scn, "p4-verdict", faults=plan("timeout", tool=REFUND),
                      rules=rules)
        call = env.call(REFUND, {"order_id": scn.env_seed["order_id"]})

        assert call.decision is not None and call.decision.action == "deny"
        assert env.injected_failures == []

    def test_call_index_counts_calls_that_reached_the_world(self, reg):
        """A call the gateway refused never touched the environment, so it cannot
        be the call that timed out. Counting it would make the plan depend on the
        policy in force rather than on the scenario."""
        rules = [Rule(rule_id="p4-no-refunds-3", lane="lane1", action="deny",
                      matcher={"tool": REFUND})]
        scn = scenario(tool_condition="all_ok")
        env = env_for(reg, scn, "p4-index",
                      faults=plan("timeout", tool=REFUND, call_index=1),
                      rules=rules)
        order_id = scn.env_seed["order_id"]
        first = env.call(REFUND, {"order_id": order_id})     # denied, not counted
        assert first.blocked and not first.faulted
        assert env.fault_report()["never_reached"], (
            "the deny consumed the plan — the fault is now unreachable")


# --------------------------------------------------------------------------- #
# planned is not fired
# --------------------------------------------------------------------------- #


class TestPlannedIsNotFired:
    def test_a_plan_the_agent_never_reaches_fires_nothing(self, reg):
        """The lesson of the whole rescue, as an assertion. The plan is real, the
        report says exactly what happened to it, and nothing anywhere claims a
        condition was exercised."""
        scn = scenario(tool_condition="all_ok")
        env = env_for(reg, scn, "p4-unreached",
                      faults=plan("timeout", tool="escalate_to_human"))
        env.call(LOOKUP, {"order_id": scn.env_seed["order_id"]})

        report = env.fault_report()
        assert report["planned"] and report["fired"] == []
        assert report["never_reached"] == report["planned"]
        assert env.injected_failures == []
        assert env.fired_faults == []
        for bin_id in FAULT_KINDS:
            assert run_predicate(f"tool_{bin_id}", trace_of(env)) is False

    def test_only_fired_kinds_reach_injected_failures(self, reg):
        """The field's rescue, stated as a property. It is derived from events,
        so nothing an agent did not meet can ever appear in it."""
        scn = scenario(tool_condition="all_ok")
        env = env_for(reg, scn, "p4-fired-only", faults=FaultPlan((
            PlannedFault(tool=LOOKUP, call_index=1, kind="timeout"),
            PlannedFault(tool="get_customer", call_index=1, kind="error_5xx"),
        ), source="explicit"))
        env.call(LOOKUP, {"order_id": scn.env_seed["order_id"]})

        assert env.injected_failures == ["timeout"]
        assert [f.fault.kind for f in env.fired_faults] == ["timeout"]

    def test_nothing_to_malform_is_recorded_as_a_skip_with_its_reason(self, reg):
        """A staged fault that could not fire is a real outcome, not a silent
        non-event: there is no response to corrupt when the call itself failed.
        The real error survives untouched, so the run still says what went
        wrong."""
        scn = scenario(tool_condition="all_ok")
        env = env_for(reg, scn, "p4-skip",
                      faults=plan("malformed_response", tool=LOOKUP))
        call = env.call(LOOKUP, {"order_id": "o-does-not-exist"})

        assert "not found" in (call.error or "")
        assert FAULT_ATTR not in call.attributes
        assert env.injected_failures == []
        skipped = env.fault_report()["skipped"]
        assert len(skipped) == 1 and "nothing to malform" in skipped[0]["reason"]

    def test_a_stale_read_with_no_cached_state_does_not_fire(self, reg):
        """There is no prior state of an order that never existed. Firing anyway
        would put a `stale_data` stamp on a "not found" — a bin credited from
        something other than what the run exhibited, which is the defect this
        phase is closing."""
        scn = scenario(tool_condition="all_ok", data_condition="entity_not_found")
        env = env_for(reg, scn, "p4-stale-missing", faults=plan("stale_data"))
        call = env.call(LOOKUP, {"order_id": scn.env_seed["order_id"]})

        assert "not found" in (call.error or "")
        assert FAULT_ATTR not in call.attributes
        assert env.injected_failures == []
        assert "no cached state" in env.fault_report()["skipped"][0]["reason"]

    def test_a_fault_staged_once_clears_and_a_persistent_one_does_not(self, reg):
        """``once`` is the difference between a blip and an outage, and the
        default is a blip: a fault that never clears makes the recovery path
        unreachable, and `traj_recovered_from_tool_failure` is half the reason
        this module exists."""
        scn = scenario(tool_condition="all_ok")
        order_id = scn.env_seed["order_id"]

        blip = env_for(reg, scn, "p4-once", faults=plan("timeout"))
        assert blip.call(LOOKUP, {"order_id": order_id}).error
        assert blip.call(LOOKUP, {"order_id": order_id}).error is None

        outage = env_for(reg, scn, "p4-persistent",
                         faults=plan("timeout", once=False))
        assert outage.call(LOOKUP, {"order_id": order_id}).error
        assert outage.call(LOOKUP, {"order_id": order_id}).error


# --------------------------------------------------------------------------- #
# replay
# --------------------------------------------------------------------------- #


class TestTheFaultedSessionReplays:
    @pytest.mark.parametrize("kind", FAULT_KINDS)
    def test_the_same_scenario_fails_the_same_call_the_same_way(self, reg, kind):
        """P1's AC-2, extended over the injector: two sessions of one scenario
        serialize to the same bytes, faults and all. A world whose failures move
        between runs is weather, and no faulted run could be frozen as a
        regression."""
        scn = scenario(23, tool_condition=kind)
        order_id = scn.env_seed["order_id"]

        def session(agent_id: str) -> str:
            env = env_for(reg, scn, agent_id)
            env.call(LOOKUP, {"order_id": order_id})
            env.call(REFUND, {"order_id": order_id, "amount_usd": 25.0})
            env.call(LOOKUP, {"order_id": order_id})
            spans = []
            for call in env.calls:
                span = json.loads(call.as_span().model_dump_json())
                # `decision_ref` is the gateway's per-decision identifier, minted
                # fresh for every evaluation (P1). It is an audit pointer, not a
                # fact about the call, and it is the one field of a scenario
                # session that is deliberately not reproducible.
                span["attributes"].pop("decision_ref", None)
                spans.append(span)
            return json.dumps({"spans": spans, "diff": env.state_diff(),
                               "faults": env.fault_report()},
                              sort_keys=True, default=str)

        first, second = session("p4-replay-a"), session("p4-replay-b")
        assert first == second

    def test_the_report_names_what_was_planned_and_what_happened(self, reg):
        """Inspectable by an operator and by a test, before and after the run.
        The two lists are separate because they answer different questions, and
        conflating them is the mistake this phase exists to undo."""
        scn = scenario(tool_condition="timeout")
        env = env_for(reg, scn, "p4-report")
        assert env.faults.as_dict()["faults"][0]["tool"] == LOOKUP

        env.call(LOOKUP, {"order_id": scn.env_seed["order_id"]})
        report = env.fault_report()
        assert report["source"] == "scenario_plan"
        assert report["fired"][0]["kind"] == "timeout"
        assert report["fired"][0]["step"] == 1
        assert report["never_reached"] == [] and report["skipped"] == []
