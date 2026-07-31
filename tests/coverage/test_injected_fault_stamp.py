"""What a fault that FIRED may credit, and what a plan may not (P4).

Five of `tool_condition`'s bins describe what the environment did to the agent,
and until the injector there was no environment that could do any of it. The CDV
loop measured the consequence exactly: with the aiming on, the targeted bin was
drawn two to three times as often and closure moved by roughly nothing, because
the top-ranked holes were those five bins and no producer could reach them.

So these tests are deliberately NOT built from hand-written spans. Every credit
below comes from a real ``ScenarioEnvironment`` call: the gateway evaluates it,
``scenario/faults.py`` stages the fault, and the span the environment records is
the one the predicate reads. A hand-built span could assert that the predicate
reads an attribute; only this can assert that anything writes one.

The invariant they exist to protect is the other direction, and it is the one P0
spent three rounds establishing: a fault the scenario PLANNED and the agent never
met is not coverage. It credits nothing and shows up as a divergence row —
requested, never exhibited.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agenttic.coverage.collect import Sample, collect
from agenttic.coverage.extractors import run_predicate
from agenttic.coverage.models.baseline import baseline_model
from agenttic.registry.sqlite_store import Registry
from agenttic.scenario import ScenarioEnvironment, install_scenario_enforcement
from agenttic.scenario.faults import FAULT_ATTR, FAULT_KINDS
from agenttic.schema.trace import Span, Trace
from agenttic.stimulus.realize import realize
from agenttic.stimulus.spaces.conversational_transactional import seed_space

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: every `tool_*` predicate, so a test can assert what a run did NOT credit as
#: well as what it did — a bin credited by accident is the defect being hunted.
TOOL_BINS = tuple(f"tool_{k}" for k in ("all_ok",) + FAULT_KINDS)


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "faults.db")


def scenario(condition: str, *, seed: int = 11, data: str = "complete"):
    return realize({"intent": "refund", "emotional_register": "neutral",
                    "data_condition": data, "policy_vector": "compliant",
                    "tool_condition": condition},
                   seed=seed, space=seed_space())


def environment(reg, condition: str, **kw) -> ScenarioEnvironment:
    """A world that stages exactly what the point asked for — via the injector's
    own ``plan_faults``, not via a plan this test wrote."""
    scn = scenario(condition, **kw)
    gateway, session = install_scenario_enforcement(reg, f"agent-{condition}")
    return ScenarioEnvironment(scn, gateway=gateway,
                               session_id=session.session_id)


def trace_of(env: ScenarioEnvironment, *, final_output: str = "here you go"
             ) -> Trace:
    """The session's calls as the trace a coverage run would see.

    One ``llm_call`` is appended because a real run has one; nothing in these
    assertions depends on it beyond keeping the trace shaped like a run.
    """
    spans = [c.as_span() for c in env.calls]
    spans.append(Span(span_id=f"s-llm", kind="llm_call", name="messages.create",
                      start_time=T0 + timedelta(seconds=99),
                      end_time=T0 + timedelta(seconds=100)))
    return Trace(trace_id="t", agent_id="a", agent_config_hash="c",
                 test_case_id="case", spans=spans, visibility="glass_box",
                 final_output=final_output)


def span(kind, name, *, i=0, input=None, output=None, error=None,
         attributes=None):
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input=input or {}, output=output or {}, error=error,
                attributes=attributes or {})


def hand_trace(*spans, final_output="here you go"):
    fixed = [s.model_copy(update={"span_id": f"s{i}"})
             for i, s in enumerate(spans)]
    return Trace(trace_id="t", agent_id="a", agent_config_hash="c",
                 test_case_id="case", spans=fixed, visibility="glass_box",
                 final_output=final_output)


# --------------------------------------------------------------------------- #
# 1. the five bins, credited by a fault that actually fired
# --------------------------------------------------------------------------- #

class TestEveryKindIsCreditedFromARealFault:
    """The starvation, ended. Each of the five kinds is staged by the injector,
    lands on a call the agent made, and credits its own bin and no other."""

    @pytest.mark.parametrize("kind", ["timeout", "error_5xx", "rate_limited",
                                      "malformed_response"])
    def test_a_fired_fault_credits_its_own_bin(self, reg, kind):
        env = environment(reg, kind)
        call = env.call("lookup_order", {"order_id": env.scenario
                                         .env_seed["order_id"]})

        assert [f.fault.kind for f in env.fired_faults] == [kind], (
            "the premise: the environment has to actually stage it")
        assert call.attributes[FAULT_ATTR] == kind
        t = trace_of(env)
        assert run_predicate(f"tool_{kind}", t, env.scenario.as_dict()) is True

    def test_stale_data_is_credited_only_when_the_agent_could_tell(self, reg):
        """`stale_data` is the one kind whose fault can fire and change nothing.

        A cached read of a record nothing has touched is byte-identical to a
        fresh one, so the agent was never exposed to staleness and there is no
        evidence for the bin to rest on. The injector is the only thing that can
        know this — it holds both payloads — and it says so on the span.
        """
        unchanged = environment(reg, "stale_data", seed=11)
        oid = unchanged.scenario.env_seed["order_id"]
        unchanged.call("lookup_order", {"order_id": oid})   # call 1, fresh
        unchanged.call("lookup_order", {"order_id": oid})   # call 2, staged
        assert unchanged.fired_faults and not unchanged.fired_faults[0].observable
        assert run_predicate("tool_stale_data", trace_of(unchanged),
                             unchanged.scenario.as_dict()) is False, (
            "a fault that made no difference is not evidence of one")

        # `stale_data` is staged on the SECOND lookup rather than the first
        # (realize._FAULT_CALL_INDEX), because a stale read is only stale
        # relative to a change and nothing has changed at a session's first call.
        # So the closing shape is the realistic hazard: read, act, re-read to
        # confirm, and be handed the world as it was before you acted.
        changed = environment(reg, "stale_data", seed=12)
        oid = changed.scenario.env_seed["order_id"]
        changed.call("lookup_order", {"order_id": oid})    # call 1, fresh
        changed.call("cancel_order", {"order_id": oid})    # the world moves on
        got = changed.call("lookup_order", {"order_id": oid})   # call 2, staged
        assert changed.fired_faults[0].observable is True
        assert got.output["status"] != "cancelled", (
            "the read has to be served from before the cancel, or nothing is "
            "stale about it")
        assert run_predicate("tool_stale_data", trace_of(changed),
                             changed.scenario.as_dict()) is True

    @pytest.mark.parametrize("kind", ["timeout", "error_5xx", "rate_limited",
                                      "malformed_response"])
    def test_a_fired_fault_credits_no_other_bin(self, reg, kind):
        """`tool_all_ok` included, and especially. It is the bin a suite gets for
        free, which is exactly why it has to stop being free the moment the
        environment is made adversarial — `malformed_response` sets no error at
        all, so without the stamp a corrupted run reads as a clean one."""
        env = environment(reg, kind)
        env.call("lookup_order", {"order_id": env.scenario.env_seed["order_id"]})
        t = trace_of(env)
        scn = env.scenario.as_dict()

        credited = [b for b in TOOL_BINS if run_predicate(b, t, scn)]
        assert credited == [f"tool_{kind}"], credited


# --------------------------------------------------------------------------- #
# 2. the anti-coverage-theatre invariant — planned is not exhibited
# --------------------------------------------------------------------------- #

class TestAPlanIsNotAnEvent:
    def test_a_fault_on_a_tool_the_agent_never_calls_credits_nothing(self, reg):
        env = environment(reg, "timeout")
        cust = next(iter(env.snapshot()["customers"]))
        env.call("get_customer", {"customer_id": cust})   # never the target

        assert env.fault_report()["never_reached"], (
            "the premise: the plan has to be real and unfired")
        t = trace_of(env)
        scn = env.scenario.as_dict()
        assert scn["injected_failures"] == [
            {"kind": "timeout", "tool": "lookup_order", "call_index": 1}], (
            "the scenario must still SAY what it planned — a silent plan cannot "
            "be reported as a divergence")
        assert run_predicate("tool_timeout", t, scn) is False

    def test_the_unfired_plan_shows_up_as_a_divergence(self, reg):
        """Requested, never exhibited — the row the two-number split exists to
        produce, and the one a stimulus-side credit would have hidden."""
        env = environment(reg, "timeout")
        cust = next(iter(env.snapshot()["customers"]))
        env.call("get_customer", {"customer_id": cust})

        report = collect(baseline_model(),
                         [Sample(trace=trace_of(env),
                                 scenario=env.scenario.as_dict(),
                                 requested=dict(env.scenario.point))])
        rows = [d for d in report.divergence()
                if d["coverpoint_id"] == "tool_condition"]
        assert rows == [{"coverpoint_id": "tool_condition", "bin_id": "timeout",
                         "requested": 1, "exhibited": 0}], rows

    def test_a_realized_plan_credits_nothing_off_an_unrelated_failure(self):
        """The residual looseness P0 disclosed, CLOSED for anything this platform
        realizes. ``error='order not found'`` under a planned timeout used to
        credit `timeout`, because the field held a bare bin name and a bare bin
        name is indistinguishable from a request. It holds an attributed plan
        now, no arm reads it, and the credit has to come from the call."""
        scn = scenario("timeout").as_dict()
        t = hand_trace(span("tool_call", "lookup_order",
                            error="order not found"))
        assert run_predicate("tool_timeout", t, scn) is False


# --------------------------------------------------------------------------- #
# 3. what the last five rounds established, re-verified
# --------------------------------------------------------------------------- #

class TestTheOldGuardsStillHold:
    def test_a_payload_containing_500_is_not_a_5xx(self):
        t = hand_trace(span("tool_call", "update_order",
                            error="could not update order #50412"))
        assert run_predicate("tool_error_5xx", t) is False

    def test_a_dollar_amount_containing_429_is_not_rate_limiting(self):
        t = hand_trace(span("tool_call", "issue_refund",
                            output={"error": "refund of $429.00 was declined"}))
        assert run_predicate("tool_rate_limited", t) is False

    def test_a_run_with_no_tool_calls_credits_no_tool_condition(self):
        """Not even under a plan. The agent never reached the environment, so the
        environment did nothing to it."""
        scn = scenario("timeout").as_dict()
        t = hand_trace(span("llm_call", "messages.create"),
                       span("error", "timeout", i=1, error="deadline exceeded"))
        for b in TOOL_BINS:
            assert run_predicate(b, t, scn) is False, b

    @pytest.mark.parametrize("kind,kw", [
        ("timeout", {"error": "deadline exceeded: no response"}),
        ("error_5xx", {"error": "502 Bad Gateway"}),
        ("rate_limited",
         {"attributes": {"http.response.status_code": 429}}),
    ])
    def test_a_genuine_fault_still_credits_without_any_stamp(self, kind, kw):
        """The stamp is an ADDITIONAL authority, never a replacement for one.
        Ingested traffic carries no injector, and a 429 is still a 429."""
        t = hand_trace(span("tool_call", "get_order", **kw))
        assert run_predicate(f"tool_{kind}", t) is True


# --------------------------------------------------------------------------- #
# 4. the two bins the extractors module calls the differentiator
# --------------------------------------------------------------------------- #

class TestRecoveryBecomesExercisable:
    """`traj_retry_after_error` and `traj_recovered_from_tool_failure` are the
    bins ``coverage/extractors.py``'s own docstring names as the thing nobody
    has — "an agent can score 100% having never once been made to recover from a
    tool failure" — and until the injector, nothing in the platform could ask for
    either. They were reachable only by a tool failing at random."""

    def test_a_retry_after_an_injected_failure_credits_both(self, reg):
        env = environment(reg, "error_5xx")
        oid = env.scenario.env_seed["order_id"]
        first = env.call("lookup_order", {"order_id": oid})
        second = env.call("lookup_order", {"order_id": oid})

        assert first.error and second.error is None, (
            "the fault must lift after it fires, or recovery is unprovable "
            "rather than untested")
        t = trace_of(env)
        assert run_predicate("traj_retry_after_error", t) is True
        assert run_predicate("traj_recovered_from_tool_failure", t) is True

    def test_the_same_scenario_without_the_retry_credits_neither(self, reg):
        """The fault fired; the agent gave up. Recovery is a thing the AGENT did,
        and a suite that injects failures without ever seeing one survived has
        measured the environment, not the agent."""
        env = environment(reg, "error_5xx")
        env.call("lookup_order", {"order_id": env.scenario.env_seed["order_id"]})

        t = trace_of(env, final_output="I could not look that up, sorry.")
        assert run_predicate("tool_error_5xx", t, env.scenario.as_dict()) is True
        assert run_predicate("traj_retry_after_error", t) is False
        assert run_predicate("traj_recovered_from_tool_failure", t) is False

    def test_a_clean_run_of_the_same_shape_credits_neither(self, reg):
        """The control arm: two identical calls, no fault. If these fired here,
        the credit above would be an artifact of calling twice."""
        env = environment(reg, "all_ok")
        oid = env.scenario.env_seed["order_id"]
        env.call("lookup_order", {"order_id": oid})
        env.call("lookup_order", {"order_id": oid})

        assert env.fired_faults == []
        t = trace_of(env)
        assert run_predicate("traj_retry_after_error", t) is False
        assert run_predicate("traj_recovered_from_tool_failure", t) is False


# --------------------------------------------------------------------------- #
# 5. the contract between the injector and the extractor
# --------------------------------------------------------------------------- #

class TestTheStampContract:
    def test_the_attribute_name_is_the_same_string_on_both_sides(self):
        """``coverage`` reads traffic from producers that have never heard of
        ``scenario``, so it spells the attribute rather than importing it. That
        makes drift possible, and this is what makes drift loud: a rename on
        either side means the credit silently never fires again."""
        from agenttic.coverage import extractors
        from agenttic.scenario import faults

        assert extractors.FAULT_ATTRIBUTE == faults.FAULT_ATTR
        assert extractors.FAULT_OBSERVABLE_ATTRIBUTE == faults.FAULT_OBSERVABLE_ATTR

    def test_every_fault_kind_has_a_bin_to_be_credited_to(self):
        from agenttic.coverage.extractors import PREDICATES

        for kind in FAULT_KINDS:
            assert f"tool_{kind}" in PREDICATES, kind

    def test_the_stamp_is_never_also_read_as_text(self):
        """A stamp is structured evidence, read structurally.

        The richer mapping form the extractor also accepts carries ids beside the
        kind, and an id is a digest: 16 hex characters give 14 three-character
        windows, so one of them contains "404" about a third of a percent of the
        time — and `data_condition` is still read by substring, so that would
        credit `entity_not_found` to a run where nothing was missing. Exactly the
        digest bug this module already measured, arriving through a new door.
        """
        t = hand_trace(span("tool_call", "lookup_order",
                            attributes={FAULT_ATTR: {
                                "kind": "malformed_response",
                                "plan_id": "404aaaaaaaaaaaaa"}}))
        assert run_predicate("tool_malformed_response", t) is True
        assert run_predicate("data_entity_not_found", t) is False

    def test_an_unrecognised_stamp_credits_nothing(self):
        """A producer stamping a kind this vocabulary does not have has not named
        a bin, and inventing one for it would be the drift the test above is
        trying to catch, laundered through a default."""
        t = hand_trace(span("tool_call", "lookup_order",
                            attributes={FAULT_ATTR: "cosmic_ray"}))
        for b in TOOL_BINS:
            if b != "tool_all_ok":
                assert run_predicate(b, t) is False, b
