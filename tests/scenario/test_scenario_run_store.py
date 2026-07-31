"""P7 — a scenario run as a durable artifact.

The registry stored the SPACE a scenario was drawn from and never the RUN. The
trace was persisted; the transcript, the fault report, the state diff and the
calls the enforcement gateway refused were assembled by ``scenario/runner.py``
and dropped on the floor when the process exited. So no CLI could show a past
run and no page could render one — the evidence existed for the length of one
function call.

Six claims are on trial here, in the order they matter:

1. a REAL run — ``realize`` → ``scenario_runner`` → ``save_scenario_run`` —
   comes back whole: the transcript, the faults that fired, the state diff;
2. **planned is not fired, and never-reached is not skipped.** Three staged
   faults with three different fates survive storage as three different facts. A
   store that flattened them would let "we staged a timeout and the agent never
   made that call" be read as "the world behaved";
3. anything DERIVABLE is re-derived on read. The stored payload is evidence and
   the summaries are computed from it, so a tampered summary loses to the
   evidence it claims to summarise;
4. absence stays visibly absent. No fault report is not an empty plan; no
   coverage collected is not coverage that credited nothing;
5. **the divergence finding is durable.** "The point asked for that corner and
   the run did not produce it" was computed live, printed once and never
   stored, so the sentence this product exists to say could not be read back off
   the row that claimed to hold the run;
6. **the counterparty's own record survives the join.** ``transcript`` is a join
   of what the agent saw with what the counterparty did, and it keeps three of
   the seven fields a turn has. The four it drops are the ones a leak is
   detected with.

Offline throughout, under a network block: a store nobody can exercise without
an API key is a store nobody exercises.
"""

from __future__ import annotations

import json
import socket

import pytest
from sqlmodel import Session as DBSession
from sqlmodel import select

from agenttic.registry.sqlite_store import (
    DuplicateVersionError, NotFoundError, Registry, ScenarioRunRow)
from agenttic.scenario.runner import (
    ScenarioAgent, ScenarioOutcome, ScriptedSupportClient,
    multi_turn_scenario_runner, scenario_runner)
from agenttic.scenario.tools import RETAIL_POLICY
from agenttic.stimulus.realize import realize
from agenttic.stimulus.spaces.conversational_transactional import seed_space

POINT = {"intent": "refund", "emotional_register": "neutral",
         "data_condition": "complete", "tool_condition": "all_ok",
         "policy_vector": "compliant"}


@pytest.fixture
def reg(tmp_path) -> Registry:
    return Registry(tmp_path / "runs.db")


@pytest.fixture
def no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network access attempted while storing a run")
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    yield


def scenario(seed: int = 7, **overrides):
    point = dict(POINT)
    point.update(overrides)
    return realize(point, seed, seed_space(), policy=RETAIL_POLICY, client=None)


def agent(agent_id: str) -> ScenarioAgent:
    return ScenarioAgent(model="scripted-support", client=ScriptedSupportClient(),
                         agent_id=agent_id)


def run_and_store(reg, scn, agent_id, *, runner=None, **save_kw) -> tuple[str, dict]:
    """One real run, stored, read back. Returns ``(run_id, outcome)``."""
    run = runner or scenario_runner()
    outcome = run(scn, adapter=agent(agent_id), store=reg)
    return reg.save_scenario_run(scn, outcome, **save_kw), outcome


def real_divergence(scn, outcome) -> list[dict]:
    """``CoverageReport.divergence()`` for ONE real run, from the same model and
    the same collector the scorecard uses.

    Not hand-made rows: the whole claim is that what the coverage engine computed
    is what the store hands back, so the rows have to come from the engine. The
    point the solver drew is passed as ``requested``, which is what makes a
    divergence row possible at all — a corner nobody asked for cannot be one the
    run failed to deliver.
    """
    from agenttic.coverage.collect import Sample, collect
    from agenttic.coverage.models.baseline import baseline_model
    report = collect(baseline_model(),
                     [Sample(trace=outcome.trace, scenario=scn.as_dict(),
                             requested=dict(scn.point))])
    return report.divergence()


#: A scenario whose ticket names an order the world does not hold. The staged
#: ``malformed_response`` reaches its call and finds an error where a response
#: should have been, which is the SKIPPED path — a fault that could not fire.
NOTHING_TO_MALFORM = dict(tool_condition="malformed_response",
                          data_condition="entity_not_found")

#: The stand-in only escalates for an out-of-scope request, so the fault staged
#: on ``lookup_order`` is never reached: the agent did not make that call.
NEVER_CALLED = dict(intent="out_of_scope", tool_condition="timeout")


# --------------------------------------------------------------------------- #
# a real run round-trips
# --------------------------------------------------------------------------- #


class TestRoundTrip:
    def test_a_real_run_survives_the_process(self, reg, no_network):
        """realize -> scenario_runner -> store -> read. The claim is only worth
        anything against a run nobody staged for it, so this one is generated
        from the space like every other."""
        scn = scenario()
        run_id, outcome = run_and_store(reg, scn, "p7-round")
        got = reg.get_scenario_run(run_id)

        assert got["scenario_id"] == scn.scenario_id
        assert got["ticket"] == scn.text
        assert got["point"] == dict(scn.point)
        assert got["seed"] == scn.seed
        assert got["space_ref"] == scn.space_ref
        assert got["space_fingerprint"] == scn.space_fingerprint
        # the trace is REFERENCED, never copied
        assert got["trace_id"] == outcome.trace.trace_id
        assert reg.get_trace(got["trace_id"]).trace_id == outcome.trace.trace_id
        assert got["derived"]["content_sha256"] == scn.content_sha256()

    def test_the_state_diff_survives(self, reg, no_network):
        """The half of correctness a judged text cannot see. This scenario's
        stand-in really does refund the order, so the diff is non-empty — and a
        fixture that stopped moving the world would make the assertion vacuous,
        so the diff is checked for content and not merely for shape."""
        scn = scenario()
        run_id, outcome = run_and_store(reg, scn, "p7-state")
        assert outcome.state_diff, "fixture no longer changes the world"

        got = reg.get_scenario_run(run_id)
        assert got["state_diff"] == outcome.state_diff
        assert got["derived"]["world_changed"] is True
        assert got["derived"]["n_changed_fields"] == len(outcome.state_diff)

    def test_a_conversation_survives_with_who_said_what(self, reg, no_network):
        """The transcript is the artifact a person reads. Speaker, words, and —
        for the counterparty — whether that turn handed over a gated fact."""
        scn = realize({**POINT, "intent": "account_change"}, 11, seed_space(),
                      policy=RETAIL_POLICY, client=None)
        run_id, outcome = run_and_store(reg, scn, "p7-conv",
                                        runner=multi_turn_scenario_runner())
        assert outcome.disclosed == ["order_id"], "fixture stopped eliciting"

        got = reg.get_scenario_run(run_id)
        assert [t["speaker"] for t in got["transcript"]] == [
            e["speaker"] for e in outcome.transcript]
        assert [t["text"] for t in got["transcript"]] == [
            e["text"] for e in outcome.transcript]

        reveals = [t for t in got["transcript"] if t.get("revealed_fact")]
        assert [t["discloses"] for t in reveals] == ["order_id"]
        # the opening asked for nothing and disclosed nothing
        assert got["transcript"][0]["kind"] == "open"
        assert got["transcript"][0]["revealed_fact"] is False
        assert got["elicitation"] == {"disclosed": ["order_id"], "withheld": []}
        assert got["derived"]["conversational"] is True
        assert got["derived"]["elicitation_complete"] is True

    def test_the_closing_turn_is_stored_and_marked_undelivered(self, reg,
                                                               no_network):
        """"Thanks, that's sorted" is something the customer said after the
        agent's last answer; the agent was never given it. Storing it is right —
        it is why the conversation ended — and drawing it as a message the agent
        ignored would be wrong, so it carries ``delivered: False`` and the turn
        count taken off the trace does not include it."""
        scn = realize({**POINT, "intent": "account_change"}, 11, seed_space(),
                      policy=RETAIL_POLICY, client=None)
        run_id, outcome = run_and_store(reg, scn, "p7-close",
                                        runner=multi_turn_scenario_runner())
        got = reg.get_scenario_run(run_id)

        closing = [t for t in got["transcript"] if t.get("kind") == "close"]
        assert len(closing) == 1 and closing[0]["delivered"] is False
        delivered = [t for t in got["transcript"]
                     if t["speaker"] == "user" and t["delivered"]]
        assert got["derived"]["n_user_turns"] == len(delivered)
        assert got["derived"]["n_user_turns"] == outcome.user_turns

    def test_a_single_shot_run_has_no_conversation_to_claim(self, reg,
                                                            no_network):
        """A ticket answered in one exchange stores an empty transcript and says
        so. It is not a conversation whose transcript went missing."""
        run_id, _ = run_and_store(reg, scenario(), "p7-single")
        got = reg.get_scenario_run(run_id)
        assert got["transcript"] == []
        assert got["session_id"] == "" and got["ended"] == ""
        assert got["derived"]["conversational"] is False
        assert got["derived"]["n_user_turns"] == 0
        # nothing was elicited because nothing was asked of it
        assert got["derived"]["elicitation_complete"] is None

    def test_blocked_calls_survive(self, reg, no_network):
        """A blocked call is the harness working. It is stored and reported, and
        the run's own count of them is derived from the list rather than stored
        beside it."""
        from agenttic.schema.enforcement import Rule
        rules = [Rule(rule_id="p7-no-refunds", lane="lane1", action="deny",
                      matcher={"tool": "issue_refund"})]
        scn = scenario()
        outcome = scenario_runner(rules=rules)(
            scn, adapter=agent("p7-blocked"), store=reg)
        assert outcome.blocked, "the deny rule matched nothing"

        got = reg.get_scenario_run(reg.save_scenario_run(scn, outcome))
        assert got["blocked"] == outcome.blocked
        assert got["derived"]["n_blocked"] == len(outcome.blocked)
        assert got["state_diff"] == {}, "a denied refund must not move money"


# --------------------------------------------------------------------------- #
# planned is not fired, and never-reached is not skipped
# --------------------------------------------------------------------------- #


class TestFaultsSurviveAsThreeDifferentFacts:
    def test_a_fired_fault_stores_as_an_event(self, reg, no_network):
        scn = scenario(tool_condition="timeout")
        run_id, outcome = run_and_store(reg, scn, "p7-fired")
        assert [f["kind"] for f in outcome.fault_report["fired"]] == ["timeout"]

        faults = reg.get_scenario_run(run_id)["faults"]
        assert faults["recorded"] is True
        assert faults["counts"] == {"planned": 1, "fired": 1, "skipped": 0,
                                    "never_reached": 0}
        assert faults["fired"][0]["kind"] == "timeout"
        assert faults["fired"][0]["tool"] == "lookup_order"
        assert faults["source"] == "scenario_plan"

    def test_a_fault_that_could_not_fire_stores_with_its_reason(self, reg,
                                                                no_network):
        """Skipped is a real outcome with a cause: there was no response to
        corrupt, because the call itself failed. The reason is what stops a
        silent non-injection from later reading as "the agent handled it"."""
        run_id, outcome = run_and_store(reg, scenario(**NOTHING_TO_MALFORM),
                                        "p7-skipped")
        faults = reg.get_scenario_run(run_id)["faults"]

        assert faults["counts"] == {"planned": 1, "fired": 0, "skipped": 1,
                                    "never_reached": 0}
        assert faults["fired"] == []
        reason = faults["skipped"][0]["reason"]
        assert "nothing to malform" in reason
        assert reason == outcome.fault_report["skipped"][0]["reason"]

    def test_a_fault_the_agent_never_reached_is_not_a_fault_that_fired(
            self, reg, no_network):
        """The sentence a UI has to be able to say: we staged this and the agent
        never got there. It is a fact about the AGENT's trajectory, where a
        skipped fault is a fact about the WORLD's state, and neither is the
        world behaving."""
        run_id, _ = run_and_store(reg, scenario(**NEVER_CALLED), "p7-unreached")
        faults = reg.get_scenario_run(run_id)["faults"]

        assert faults["counts"] == {"planned": 1, "fired": 0, "skipped": 0,
                                    "never_reached": 1}
        assert faults["never_reached"][0]["tool"] == "lookup_order"
        assert faults["never_reached"][0]["kind"] == "timeout"
        assert faults["fired"] == [] and faults["skipped"] == []

    def test_the_three_fates_are_three_different_stored_shapes(self, reg,
                                                               no_network):
        """Read side by side, because that is how a reader meets them. If any
        two of these collapsed the store would be lying about a run."""
        fates = {}
        for name, overrides in (("fired", {"tool_condition": "timeout"}),
                                ("skipped", NOTHING_TO_MALFORM),
                                ("never_reached", NEVER_CALLED)):
            run_id, _ = run_and_store(reg, scenario(**overrides), f"p7-{name}")
            fates[name] = reg.get_scenario_run(run_id)["faults"]["counts"]

        assert fates["fired"]["fired"] == 1
        assert fates["skipped"]["skipped"] == 1
        assert fates["never_reached"]["never_reached"] == 1
        assert len({json.dumps(c, sort_keys=True) for c in fates.values()}) == 3

    def test_a_clean_run_reports_a_plan_of_nothing(self, reg, no_network):
        """``all_ok`` stages nothing, and that IS a recorded report — four empty
        lists — rather than the absence of one."""
        run_id, _ = run_and_store(reg, scenario(), "p7-clean")
        faults = reg.get_scenario_run(run_id)["faults"]
        assert faults["recorded"] is True
        assert faults["source"] == "none"
        assert faults["counts"] == {"planned": 0, "fired": 0, "skipped": 0,
                                    "never_reached": 0}


# --------------------------------------------------------------------------- #
# derive, do not trust
# --------------------------------------------------------------------------- #


def _tamper(reg, run_id: str, mutate) -> None:
    """Rewrite one stored payload in place. Not a supported operation — the
    point is that a row which HAS been rewritten cannot make the read agree."""
    with DBSession(reg.engine) as s:
        row = s.exec(select(ScenarioRunRow).where(
            ScenarioRunRow.run_id == run_id)).one()
        data = json.loads(row.payload)
        mutate(data)
        row.payload = json.dumps(data)
        s.add(row)
        s.commit()


class TestDerivedFieldsAreRecomputed:
    def test_a_tampered_never_reached_loses_to_the_evidence(self, reg,
                                                            no_network):
        """``never_reached`` is a function of planned/fired/skipped. A stored
        copy is a fourth list that can disagree with the three it came from, and
        it is the one that carries the claim — so it is recomputed, and the
        stored value cannot survive contradicting its own evidence."""
        run_id, _ = run_and_store(reg, scenario(**NEVER_CALLED), "p7-derive-1")
        _tamper(reg, run_id, lambda d: d["fault_report"].__setitem__(
            "never_reached", []))

        faults = reg.get_scenario_run(run_id)["faults"]
        assert faults["counts"]["never_reached"] == 1
        assert faults["never_reached"][0]["tool"] == "lookup_order"

    def test_an_invented_fired_fault_cannot_be_claimed_by_the_summary(
            self, reg, no_network):
        """The mirror: the counts come from the lists, so a payload cannot
        report a fault that fired without an event to show for it."""
        run_id, _ = run_and_store(reg, scenario(**NEVER_CALLED), "p7-derive-2")
        _tamper(reg, run_id, lambda d: d["fault_report"].__setitem__(
            "counts", {"fired": 99}))

        faults = reg.get_scenario_run(run_id)["faults"]
        assert faults["counts"]["fired"] == 0 and faults["fired"] == []

    def test_a_fault_report_naming_a_tool_the_world_lacks_is_not_served(
            self, reg, no_network):
        """Reconstruction validates. A payload describing an impossible fault
        cannot be rendered as though it described a real one — the evidence is
        still returned, the derivation is reported as unavailable, and nothing
        is invented in its place."""
        run_id, _ = run_and_store(reg, scenario(tool_condition="timeout"),
                                  "p7-derive-3")

        def bad(d):
            d["fault_report"]["planned"][0]["tool"] = "delete_everything"

        _tamper(reg, run_id, bad)
        faults = reg.get_scenario_run(run_id)["faults"]
        assert faults["recorded"] is True
        assert faults["never_reached"] is None and faults["counts"] is None
        assert "delete_everything" in faults["problem"]
        assert faults["planned"][0]["tool"] == "delete_everything"

    def test_recomputing_never_reached_does_not_launder_the_evidence(
            self, reg, no_network):
        """Deriving one field must not quietly rewrite the other three. The
        stored lists are returned as stored — a field this module has never heard
        of still reaches the reader, because code that discards an input owes a
        disclosure and silently normalising it away would not be one."""
        run_id, _ = run_and_store(reg, scenario(tool_condition="timeout"),
                                  "p7-nolaunder")

        def annotate(d):
            d["fault_report"]["fired"][0]["seen_by"] = "some future field"

        _tamper(reg, run_id, annotate)
        faults = reg.get_scenario_run(run_id)["faults"]
        assert faults["fired"][0]["seen_by"] == "some future field"
        assert faults["counts"]["fired"] == 1

    def test_elicitation_completeness_is_recomputed_not_stored(self, reg,
                                                               no_network):
        """Satisfied AND nothing still withheld — one definition, the
        counterparty's own. A run that ends satisfied with a fact never elicited
        is not complete, and no stored flag gets to say otherwise."""
        scn = realize({**POINT, "intent": "account_change"}, 11, seed_space(),
                      policy=RETAIL_POLICY, client=None)
        run_id, _ = run_and_store(reg, scn, "p7-elicit",
                                  runner=multi_turn_scenario_runner())
        assert reg.get_scenario_run(run_id)["derived"][
            "elicitation_complete"] is True

        _tamper(reg, run_id, lambda d: d.__setitem__("withheld", ["order_id"]))
        assert reg.get_scenario_run(run_id)["derived"][
            "elicitation_complete"] is False

    def test_the_content_hash_is_recomputed_from_the_stored_scenario(
            self, reg, no_network):
        """The hash says which scenario this run was. Recomputed from the stored
        ticket/point/seed/fingerprint, so a row cannot claim a provenance its own
        contents contradict."""
        scn = scenario()
        run_id, _ = run_and_store(reg, scn, "p7-hash")
        assert reg.get_scenario_run(run_id)["derived"][
            "content_sha256"] == scn.content_sha256()

        _tamper(reg, run_id, lambda d: d.__setitem__("ticket", "something else"))
        assert reg.get_scenario_run(run_id)["derived"][
            "content_sha256"] != scn.content_sha256()

    def test_the_turn_count_comes_off_the_trace(self, reg, no_network):
        """``ScenarioOutcome.user_turns`` documents the trace as the authority on
        how many turns a run exhibited, because that is what coverage counts.
        The stored record reads the same authority rather than counting its own
        transcript, so the two cannot disagree."""
        scn = realize({**POINT, "intent": "account_change"}, 11, seed_space(),
                      policy=RETAIL_POLICY, client=None)
        run_id, outcome = run_and_store(reg, scn, "p7-turns",
                                        runner=multi_turn_scenario_runner())
        assert outcome.user_turns == 2, "fixture stopped taking two turns"

        _tamper(reg, run_id, lambda d: d.__setitem__("transcript", []))
        got = reg.get_scenario_run(run_id)
        assert got["transcript"] == []
        assert got["derived"]["n_user_turns"] == 2


# --------------------------------------------------------------------------- #
# absence stays absent
# --------------------------------------------------------------------------- #


class TestAbsenceIsNotZero:
    def test_no_fault_report_is_not_an_empty_plan(self, reg, no_network):
        """A run stored without a fault report reads ``recorded: False`` with
        four ``null`` lists. Rendering it as "0 faults planned" would turn "we
        did not write down what was staged" into "the world was never asked to
        fail", which is a claim about the run nobody made."""
        scn = scenario()
        outcome = scenario_runner()(scn, adapter=agent("p7-noreport"), store=reg)
        bare = ScenarioOutcome(trace=outcome.trace, state_diff=outcome.state_diff)
        assert bare.fault_report == {}

        faults = reg.get_scenario_run(
            reg.save_scenario_run(scn, bare))["faults"]
        assert faults["recorded"] is False
        assert faults["planned"] is None and faults["fired"] is None
        assert faults["skipped"] is None and faults["never_reached"] is None
        assert faults["counts"] is None

    def test_uncollected_coverage_is_not_coverage_that_credited_nothing(
            self, reg, no_network):
        """Two runs, one measured and one not. ``measured: false, bins: null``
        and ``measured: true, bins: []`` are different claims and must not
        arrive looking the same.

        ``divergence`` joined the block after this test was written and none of
        these three runs recorded one, so it is ``null`` on all of them — which
        is the assertion, not an omission: a coverage read that answered the
        divergence question by itself would be the fabrication next door.
        """
        run_a, _ = run_and_store(reg, scenario(seed=7), "p7-cov-a")
        run_b, _ = run_and_store(reg, scenario(seed=8), "p7-cov-b",
                                 exhibited_bins=[])
        run_c, _ = run_and_store(reg, scenario(seed=9), "p7-cov-c",
                                 exhibited_bins=["trajectory:tool_then_answer"])

        # `model` joined this block — which model's vocabulary the bins are
        # in. These runs were stored without one, so it reads None, and the
        # three-state split the test is about is unchanged.
        assert reg.get_scenario_run(run_a)["coverage"] == {
            "measured": False, "bins": None, "divergence": None, "model": None}
        assert reg.get_scenario_run(run_b)["coverage"] == {
            "measured": True, "bins": [], "divergence": None, "model": None}
        assert reg.get_scenario_run(run_c)["coverage"] == {
            "measured": True, "bins": ["trajectory:tool_then_answer"],
            "divergence": None, "model": None}

    def test_a_missing_trace_leaves_the_turn_count_unknown(self, reg,
                                                           no_network):
        """Zero would be a measurement. A trace that cannot be read is an
        uncounted run, and it says so instead."""
        scn = realize({**POINT, "intent": "account_change"}, 11, seed_space(),
                      policy=RETAIL_POLICY, client=None)
        run_id, outcome = run_and_store(reg, scn, "p7-notrace",
                                        runner=multi_turn_scenario_runner())
        from agenttic.registry.sqlite_store import TraceRow
        with DBSession(reg.engine) as s:
            s.delete(s.exec(select(TraceRow).where(
                TraceRow.trace_id == outcome.trace.trace_id)).one())
            s.commit()

        got = reg.get_scenario_run(run_id)
        assert got["derived"]["n_user_turns"] is None
        assert any(d.get("kind") == "trace_missing" for d in got["disclosures"])

    def test_delivered_reads_the_turn_kind_and_not_the_words(self, reg,
                                                             no_network):
        """A customer asking to CLOSE their account is not a closing turn. The
        flag is decided by the turn's kind, matched whole; a transcript line
        whose text merely contains the word is still a turn the agent was
        handed."""
        scn = scenario()
        outcome = scenario_runner()(scn, adapter=agent("p7-token"), store=reg)
        spoken = ScenarioOutcome(
            trace=outcome.trace, session_id="sess-token",
            transcript=[{"speaker": "user", "kind": "open",
                         "text": "please close my account and disclose nothing",
                         "discloses": ""},
                        {"speaker": "user", "kind": "close", "text": "bye",
                         "discloses": ""}])
        got = reg.get_scenario_run(reg.save_scenario_run(scn, spoken))

        assert got["transcript"][0]["delivered"] is True
        assert got["transcript"][0]["revealed_fact"] is False
        assert got["transcript"][1]["delivered"] is False


# --------------------------------------------------------------------------- #
# asked for, never exhibited
# --------------------------------------------------------------------------- #


class TestDivergenceSurvivesTheProcess:
    """``CoverageReport.divergence()`` names the corners the point REQUESTED and
    the trace never EXHIBITED. It was computed from the live in-memory report,
    printed once, and never written down — so the command that stored the run
    could say it and the row it stored could not, which is the invariant
    ``scenario run`` states about itself twelve lines earlier ("a fact the store
    dropped cannot be shown by the command that stored it").
    """

    def test_the_corner_the_point_asked_for_and_never_got_is_readable_later(
            self, reg, no_network):
        """The whole point. This scenario asks for a ``timeout`` on
        ``lookup_order`` and the stand-in never makes that call, so the corner
        was requested and never produced — and that has to survive the process
        that noticed it."""
        scn = scenario(**NEVER_CALLED)
        outcome = scenario_runner()(scn, adapter=agent("p7-div"), store=reg)
        rows = real_divergence(scn, outcome)
        assert rows, "the fixture stopped diverging — the assertion is vacuous"
        assert {"coverpoint_id", "bin_id", "requested", "exhibited"} <= set(
            rows[0]), rows

        run_id = reg.save_scenario_run(scn, outcome, divergence=rows)
        stored = reg.get_scenario_run(run_id)["coverage"]["divergence"]
        assert stored == rows            # verbatim, the method's own dicts
        assert [(d["coverpoint_id"], d["bin_id"]) for d in stored] == [
            ("tool_condition", "timeout")]

    def test_the_three_divergence_states_are_three_different_stored_shapes(
            self, reg, no_network):
        """Not recorded, recorded-and-empty, and recorded-with-findings. A
        reader holding only the row must be able to tell which of the three it
        has: ``null`` means nobody asked this run whether it diverged, and
        printing that as "nothing diverged" is an absence sold as a result."""
        none_id, _ = run_and_store(reg, scenario(seed=7), "p7-div-none")
        empty_id, _ = run_and_store(reg, scenario(seed=8), "p7-div-empty",
                                    divergence=[])

        scn = scenario(**NEVER_CALLED)
        outcome = scenario_runner()(scn, adapter=agent("p7-div-found"), store=reg)
        found_id = reg.save_scenario_run(
            scn, outcome, divergence=real_divergence(scn, outcome))

        blocks = [reg.get_scenario_run(r)["coverage"]
                  for r in (none_id, empty_id, found_id)]
        assert blocks[0]["divergence"] is None
        assert blocks[1]["divergence"] == []
        assert blocks[2]["divergence"]
        assert len({json.dumps(b, sort_keys=True) for b in blocks}) == 3

    def test_measured_does_not_answer_the_divergence_question(self, reg,
                                                              no_network):
        """``measured`` speaks for ``bins`` and for nothing else. A run whose
        bins were collected and whose divergence was not is exactly the row a
        single flag would misdescribe — it would read ``measured: true`` beside
        a ``null`` nobody computed, and the null would look like a finding of
        none."""
        run_id, _ = run_and_store(reg, scenario(seed=7), "p7-div-split",
                                  exhibited_bins=["trajectory:tool_then_answer"])
        cov = reg.get_scenario_run(run_id)["coverage"]
        assert cov["measured"] is True and cov["bins"]
        assert cov["divergence"] is None

    def test_an_empty_divergence_is_a_measurement_and_not_an_absence(
            self, reg, no_network):
        """A clean run whose every requested corner appeared. It was asked, and
        the answer was "nothing" — which is a finding, and is not the same row
        as the one nobody asked."""
        scn = scenario()
        outcome = scenario_runner()(scn, adapter=agent("p7-div-clean"), store=reg)
        rows = real_divergence(scn, outcome)
        assert rows == [], "the clean fixture started diverging"

        run_id = reg.save_scenario_run(scn, outcome, divergence=rows)
        cov = reg.get_scenario_run(run_id)["coverage"]
        assert cov["divergence"] == []
        assert cov["divergence"] is not None


# --------------------------------------------------------------------------- #
# the counterparty's own record
# --------------------------------------------------------------------------- #


class TestTheCounterpartyRecordSurvivesTheJoin:
    """``transcript`` is a JOIN — what the agent saw, paired with what the
    counterparty did — and it keeps three of the seven fields a ``UserTurn``
    has. Storing only the join discarded the other four silently.
    """

    def test_the_turns_are_stored_and_not_only_the_join(self, reg, no_network):
        scn = realize({**POINT, "intent": "account_change"}, 11, seed_space(),
                      policy=RETAIL_POLICY, client=None)
        run_id, outcome = run_and_store(reg, scn, "p7-turns-kept",
                                        runner=multi_turn_scenario_runner())
        assert outcome.turns, "fixture stopped taking turns"

        stored = reg.get_scenario_run(run_id)["turns"]
        # through JSON, because that is how the row travels: a tuple stores and
        # reads back as a list, and nothing else about a turn may change.
        assert stored == json.loads(json.dumps(outcome.turns))

    def test_the_four_fields_the_join_drops_are_the_ones_worth_keeping(
            self, reg, no_network):
        """Field by field, against a real conversation. ``expect``/``forbid``
        carry the values a reply must and must not contain, ``reason`` says why
        a close happened, ``source`` says which simulator produced THAT turn —
        and not one of them is anywhere in the transcript."""
        scn = realize({**POINT, "intent": "account_change"}, 11, seed_space(),
                      policy=RETAIL_POLICY, client=None)
        run_id, _ = run_and_store(reg, scn, "p7-turns-fields",
                                  runner=multi_turn_scenario_runner())
        got = reg.get_scenario_run(run_id)
        turns = got["turns"]

        assert any(t["forbid"] for t in turns), "no turn withheld anything"
        assert any(t["expect"] for t in turns), "no turn expected anything back"
        closes = [t for t in turns if t["kind"] == "close"]
        assert [t["reason"] for t in closes] == ["satisfied"]
        assert {t["source"] for t in turns} == {"scripted"}

        dropped = {"expect", "forbid", "reason", "source"}
        assert all(dropped.isdisjoint(entry) for entry in got["transcript"])

    def test_a_leak_can_still_be_graded_from_the_stored_row(self, reg,
                                                            no_network):
        """Why ``forbid`` is not decoration. An agent that states a value it was
        never told did not deduce it, and ``UserTurn.grade`` is how that is
        caught — so a stored run that dropped ``forbid`` could never be
        re-graded, and the check would exist only for the length of the process
        that ran it."""
        from agenttic.scenario.user import UserTurn

        scn = realize({**POINT, "intent": "account_change"}, 11, seed_space(),
                      policy=RETAIL_POLICY, client=None)
        run_id, _ = run_and_store(reg, scn, "p7-turns-grade",
                                  runner=multi_turn_scenario_runner())
        opening = reg.get_scenario_run(run_id)["turns"][0]
        assert opening["kind"] == "open" and opening["forbid"]

        secret = opening["forbid"][0]
        rebuilt = UserTurn(**{**opening, "expect": tuple(opening["expect"]),
                              "forbid": tuple(opening["forbid"])})
        assert rebuilt.grade(f"Sure — that's order {secret}.")["leaked"] == [
            secret]
        assert rebuilt.grade("Which order is this about?")["leaked"] == []

    def test_a_single_shot_run_records_no_turns_and_that_is_a_record(
            self, reg, no_network):
        """``[]`` here is honest: there was no conversation, so the counterparty
        took no turns. It is not the same row as one whose turn record was never
        kept."""
        run_id, _ = run_and_store(reg, scenario(), "p7-turns-single")
        assert reg.get_scenario_run(run_id)["turns"] == []

    def test_a_row_that_never_kept_a_turn_record_reads_null_not_empty(
            self, reg, no_network):
        """The back-compat state, forced onto a real row: a payload written
        before ``turns`` was stored has no such key, and must not read back as a
        counterparty that said nothing. ``_tamper`` is used to remove the key
        rather than to corrupt a value — the row it produces is exactly the row
        the previous serializer wrote."""
        scn = realize({**POINT, "intent": "account_change"}, 11, seed_space(),
                      policy=RETAIL_POLICY, client=None)
        run_id, outcome = run_and_store(reg, scn, "p7-turns-legacy",
                                        runner=multi_turn_scenario_runner())
        assert outcome.turns, "fixture stopped taking turns"
        _tamper(reg, run_id, lambda d: d.pop("turns"))

        got = reg.get_scenario_run(run_id)
        assert got["turns"] is None
        # and the transcript is still there, so "no record of the turns" is
        # visibly not "no conversation happened"
        assert [t["speaker"] for t in got["transcript"]].count("user") == 3

        single, _ = run_and_store(reg, scenario(), "p7-turns-legacy-vs-single")
        assert reg.get_scenario_run(single)["turns"] == []


# --------------------------------------------------------------------------- #
# the storage rules
# --------------------------------------------------------------------------- #


class TestStorageRules:
    def test_a_run_whose_trace_was_never_stored_is_refused(self, reg,
                                                           no_network):
        """An orphan looks saved and is permanently half-readable. Refused at
        the boundary a mistake cannot be walked back across."""
        scn = scenario()
        outcome = scenario_runner(persist=False)(
            scn, adapter=agent("p7-orphan"), store=reg)
        with pytest.raises(NotFoundError):
            reg.save_scenario_run(scn, outcome)
        assert reg.list_scenario_runs() == []

    def test_a_run_is_immutable(self, reg, no_network):
        scn = scenario()
        run_id, outcome = run_and_store(reg, scn, "p7-immutable")
        with pytest.raises(DuplicateVersionError):
            reg.save_scenario_run(scn, outcome)
        assert len(reg.list_scenario_runs()) == 1

    def test_one_trace_is_one_run(self, reg, no_network):
        """A second row under a different id would be a second account of one
        trace, and a reader would have no basis for choosing between them."""
        scn = scenario()
        _, outcome = run_and_store(reg, scn, "p7-onetrace")
        with pytest.raises(DuplicateVersionError):
            reg.save_scenario_run(scn, outcome, run_id="a-different-id")

    def test_the_run_id_defaults_to_the_trace_id(self, reg, no_network):
        run_id, outcome = run_and_store(reg, scenario(), "p7-runid")
        assert run_id == outcome.trace.trace_id

    def test_the_agent_is_read_off_the_trace(self, reg, no_network):
        """One authority on who ran. The row cannot name an agent the trace does
        not."""
        run_id, outcome = run_and_store(reg, scenario(), "p7-agentid")
        got = reg.get_scenario_run(run_id)
        assert got["agent_id"] == outcome.trace.agent_id == "p7-agentid"

    def test_an_unknown_run_is_not_found(self, reg):
        assert reg.find_scenario_run("nope") is None
        with pytest.raises(NotFoundError):
            reg.get_scenario_run("nope")

    def test_runs_are_tenant_scoped(self, tmp_path, no_network):
        """Postgres shares one database across tenants; the row-level scope is
        what keeps one tenant's runs out of another's list."""
        db = tmp_path / "shared.db"
        one = Registry(db, tenant="t1")
        two = Registry(db, tenant="t2")
        run_id, _ = run_and_store(one, scenario(), "p7-tenant")

        assert [r["run_id"] for r in one.list_scenario_runs()] == [run_id]
        assert two.list_scenario_runs() == []
        assert two.find_scenario_run(run_id) is None

    def test_the_list_is_newest_first_and_filterable(self, reg, no_network):
        first, _ = run_and_store(reg, scenario(seed=7), "p7-list-a")
        second, _ = run_and_store(reg, scenario(seed=8), "p7-list-b")

        rows = reg.list_scenario_runs()
        assert [r["run_id"] for r in rows] == [second, first]
        assert [r["run_id"] for r in reg.list_scenario_runs(
            agent_id="p7-list-a")] == [first]
        assert [r["run_id"] for r in reg.list_scenario_runs(limit=1)] == [second]

        scn = scenario(seed=8)
        assert [r["run_id"] for r in reg.list_scenario_runs(
            scenario_id=scn.scenario_id)] == [second]

    def test_the_list_summary_is_derived_from_the_payload(self, reg,
                                                          no_network):
        """The list is a projection of the same evidence, not a second set of
        columns that can drift from it."""
        run_id, _ = run_and_store(reg, scenario(**NEVER_CALLED), "p7-listderive")
        row = reg.list_scenario_runs()[0]
        detail = reg.get_scenario_run(run_id)

        assert row["faults"]["counts"] == detail["faults"]["counts"]
        assert row["world_changed"] == detail["derived"]["world_changed"]
        assert row["conversational"] == detail["derived"]["conversational"]
        assert row["n_blocked"] == detail["derived"]["n_blocked"]
