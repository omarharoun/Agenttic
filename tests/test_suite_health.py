"""Diagnosing the instrument instead of the agent.

Every other number in the harness is a statement about an agent. A case no
agent has ever passed is a statement about the CASE — and it is the one thing
per-agent scoring structurally cannot see, because from inside a single run it
is indistinguishable from a weak agent.

The tests that matter most here are the ones about what the diagnostic REFUSES
to say. A "broken case" detector that fires on one bad run manufactures findings,
and a fabricated defect costs more than a missed one: it sends someone to edit a
correct test case.
"""

from __future__ import annotations

from agenttic.metrics.suite_health import (MIN_AGENTS, blocked_reason,
                                           case_evidence, suite_health)


def run(agent: str, per_case: dict, run_id: str = "r") -> dict:
    return {"run_id": f"{run_id}-{agent}", "agent_id": agent, "per_case": per_case}


class TestTheEvidenceBar:
    def test_one_agent_failing_a_case_says_NOTHING_about_the_case(self):
        """The whole reason this is a cross-agent diagnostic. One agent that
        fails everything is one agent — reporting its failures as broken cases
        would rewrite a correct suite to match a bad model."""
        h = suite_health([run("solo", {"t1": [False, False, False]})])
        assert h["unpassed"] == []
        assert h["insufficient_evidence"] == ["t1"]

    def test_two_agents_are_enough_to_speak(self):
        h = suite_health([run("a", {"t1": [False]}), run("b", {"t1": [False]})])
        assert h["unpassed"] == ["t1"]

    def test_the_bar_is_stated_in_the_finding_not_just_enforced(self):
        h = suite_health([run("solo", {"t1": [False]})])
        note = h["findings"][0]["note"]
        assert "1 agent" in note and str(MIN_AGENTS) in note

    def test_a_history_below_the_bar_is_blocked_with_a_reason(self):
        h = suite_health([run("solo", {"t1": [False]})])
        assert blocked_reason(h) is not None
        assert "2 are needed" in blocked_reason(h)

    def test_a_history_that_can_be_read_is_not_blocked(self):
        h = suite_health([run("a", {"t1": [True]}), run("b", {"t1": [False]})])
        assert blocked_reason(h) is None


class TestTheUnpassedCase:
    def test_a_case_no_agent_ever_passed_is_named(self):
        runs = [run("a", {"good": [True], "bad": [False]}),
                run("b", {"good": [True], "bad": [False]}),
                run("c", {"good": [False], "bad": [False]})]
        h = suite_health(runs)
        assert h["unpassed"] == ["bad"]

    def test_one_pass_by_one_agent_clears_it(self):
        """Passed once => not impossible. Whether it passes RELIABLY is a
        different question, already answered by flaky_rate — conflating them
        would report every flaky case as broken."""
        runs = [run("a", {"t1": [False, False, True]}), run("b", {"t1": [False]})]
        h = suite_health(runs)
        assert h["unpassed"] == []
        assert h["discriminating"] == 1

    def test_the_finding_refuses_to_decide_WHICH_explanation_it_is(self):
        """The direction of the inference is exactly what is unknown. A hard
        case and a broken case produce identical evidence here."""
        runs = [run("a", {"t1": [False]}), run("b", {"t1": [False]})]
        note = suite_health(runs)["findings"][0]["note"]
        assert "hardest case" in note and "broken one" in note
        assert "cannot tell you which" in note

    def test_it_carries_the_evidence_the_verdict_rests_on(self):
        runs = [run("a", {"t1": [False, False]}), run("b", {"t1": [False]})]
        f = suite_health(runs)["findings"][0]
        assert sorted(f["agents"]) == ["a", "b"] and f["trials"] == 3
        assert f["passes"] == 0 and f["pass_rate"] == 0.0


class TestTheInertCase:
    def test_a_case_everyone_always_passes_is_flagged_as_paying_for_nothing(self):
        runs = [run("a", {"t1": [True, True]}), run("b", {"t1": [True]})]
        h = suite_health(runs)
        assert h["inert"] == ["t1"]
        assert "separates nobody" in h["findings"][0]["note"]

    def test_a_single_failure_anywhere_makes_it_discriminating(self):
        runs = [run("a", {"t1": [True, True]}), run("b", {"t1": [True, False]})]
        h = suite_health(runs)
        assert h["inert"] == [] and h["discriminating"] == 1


class TestItDoesNotLieAboutWhatItRead:
    def test_runs_with_no_per_case_are_COUNTED_not_silently_dropped(self):
        """The history predates per-case persistence. A diagnosis that read 2 of
        40 runs while printing a confident verdict is this module's own failure
        mode, committed by this module."""
        runs = [run("a", {"t1": [False]}), run("b", {"t1": [False]}),
                {"run_id": "old", "agent_id": "c"}]
        h = suite_health(runs)
        assert h["runs_read"] == 2 and h["runs_without_per_case"] == 1

    def test_an_empty_history_concludes_nothing_and_says_why(self):
        h = suite_health([])
        assert h["cases"] == 0 and h["unpassed"] == []
        assert "no canonical run carries per-case results" in blocked_reason(h)

    def test_a_malformed_per_case_is_skipped_AND_uncounted_as_an_agent(self):
        """Skipping it is not enough — if the malformed run still counted toward
        the agent bar, one usable agent plus one unreadable one would clear a
        threshold that exists to require two OBSERVATIONS."""
        h = suite_health([run("a", {"t1": "nope"}), run("b", {"t1": [True]})])
        assert h["cases"] == 1
        assert case_evidence([run("a", {"t1": "nope"}),
                              run("b", {"t1": [True]})])["t1"]["agents"] == ["b"]
        assert h["findings"][0]["verdict"] == "insufficient_evidence"

    def test_only_the_named_agents_are_reported(self):
        h = suite_health([run("a", {"t1": [True]}), run("b", {"t1": [False]})])
        assert h["agents"] == ["a", "b"]


class TestCaseEvidence:
    def test_an_agent_that_ran_ten_times_is_still_ONE_agent(self):
        """The distinction the verdict rests on: trials are not independent
        observations of the case, agents are."""
        runs = [run("a", {"t1": [False] * 10}, run_id=f"r{i}") for i in range(3)]
        ev = case_evidence(runs)
        assert ev["t1"]["agents"] == ["a"]
        assert ev["t1"]["trials"] == 30
        assert suite_health(runs)["unpassed"] == []

    def test_passing_is_recorded_per_agent(self):
        ev = case_evidence([run("a", {"t1": [False, True]}),
                            run("b", {"t1": [False]})])
        assert ev["t1"]["agent_pass"] == {"a": True, "b": False}


class TestItChangesNoScore:
    def test_the_diagnosis_never_touches_a_run(self):
        runs = [run("a", {"t1": [False]}), run("b", {"t1": [False]})]
        before = [dict(r) for r in runs]
        suite_health(runs)
        assert runs == before

    def test_it_says_so_where_a_reader_will_see_it(self):
        h = suite_health([run("a", {"t1": [False]}), run("b", {"t1": [False]})])
        assert "changes a score" in h["note"]


class TestThePersistenceThatFeedsIt:
    def test_run_standard_op_now_keeps_per_case(self):
        """Without this the diagnostic has no evidence, forever: the vectors were
        computed on every run and dropped at the return."""
        import inspect

        from agenttic import ops

        src = inspect.getsource(ops.run_standard_op)
        assert "include_per_case=True" in src

    def test_per_case_is_a_top_level_key_not_a_component(self):
        """`compute_index` intersects with index_weights(), but the rule worth
        pinning is that nothing about suite health can reach a published Index."""
        from agenttic.metrics.catalog import index_weights

        assert "per_case" not in index_weights()
