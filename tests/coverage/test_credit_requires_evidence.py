"""A coverage bin may be credited only by evidence that the thing HAPPENED.

Not by a substring in an error message, not by a tool's NAME, not by a scenario
declaring an intent, and not by a run that never reached the environment. Round 2
applied that rule to the status-code half of `tool_condition`; these tests apply
it to the three places it was never applied:

* `action_risk.read_only` — a CLAIM ABOUT SAFETY, credited on tools nobody could
  classify. `frobnicate` and `mcp__acme__run` each fired it, so a suite of opaque
  tool names reported "the run touched tools and changed nothing" about calls that
  could be money transfers.
* `trajectory.escalated_to_human` — credited by a span NAME with no outcome, so a
  handoff that was REFUSED, one merely CONSULTED, and a FAQ merely READ all
  claimed the escalation path had been exercised.
* the phrase half of `tool_condition` — five real tool-failure messages each
  credited a condition they deny, two of them with the direction inverted (the
  bin means the tool's RESPONSE was malformed; the message says the agent's
  REQUEST was).

Every test here is written in both directions on purpose. Refusing a false credit
is only half the property: a predicate that credits nothing is exactly as
dishonest as one that credits everything, and much easier to write by accident.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agenttic.coverage.collect import Sample, collect
from agenttic.coverage.extractors import run_predicate
from agenttic.coverage.models.baseline import baseline_model
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def span(kind, name, *, i=0, input=None, output=None, error=None, attributes=None):
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input=input or {}, output=output or {}, error=error,
                attributes=attributes or {})


def trace(*spans, final_output="here you go"):
    fixed = [s.model_copy(update={"span_id": f"s{i}"}) for i, s in enumerate(spans)]
    return Trace(trace_id="t", agent_id="a", agent_config_hash="c",
                 test_case_id="case", spans=fixed, visibility="glass_box",
                 final_output=final_output)


# --------------------------------------------------------------------------- #
# 1. action_risk.read_only must be EARNED
# --------------------------------------------------------------------------- #

#: Every one of these fired `action_read_only` before this change. They are not
#: adversarial: `run_workflow`, `execute` and `process_request` are ordinary
#: names, `tool_0` is what a generated harness emits, and `mcp__acme__*` is the
#: literal shape the MCP client produces for a third-party server's tools.
OPAQUE = ("frobnicate", "run_workflow", "execute", "tool_0", "do_thing",
          "process_request", "mcp__acme__apply", "mcp__acme__run",
          "handle", "dispatch", "invoke", "step_2")


@pytest.mark.parametrize("tool_name", OPAQUE)
def test_an_unclassifiable_tool_is_never_read_only(tool_name):
    """`is_write` is a name-hint matcher, so its negative answer is SILENCE.

    Reading silence as "changed nothing" is the single worst available defect on
    this product: read_only is the bin an operator points at to say the agent is
    safe, and it was being credited for a call that could be a money transfer.
    """
    t = trace(span("tool_call", tool_name), span("llm_call", "llm", i=1))
    assert run_predicate("action_read_only", t) is False


@pytest.mark.parametrize("tool_name", [
    "get_order", "list_invoices", "search_kb", "lookup_account",
    "fetch_profile", "read_policy", "query_orders", "describe_account"])
def test_a_read_named_tool_is_still_read_only(tool_name):
    """The other direction, and the one an over-correction would break.

    A read hint is evidence of exactly the same grade and kind as the write hint
    that credits `mutating_reversible`. Rejecting one while accepting the other
    would not be stricter — it would make `read_only` unreachable for every
    uninstrumented suite, turning an over-report into an under-report of the same
    size.
    """
    t = trace(span("tool_call", tool_name), span("llm_call", "llm", i=1))
    assert run_predicate("action_read_only", t) is True


def test_an_explicit_non_mutating_attribute_is_evidence():
    """Silence is not a guarantee; a producer saying "no, this does not mutate"
    is. This is the arm that lets an opaque name be credited honestly, and the
    fix an operator is told to apply."""
    t = trace(span("tool_call", "process_request", attributes={"mutating": False}))
    assert run_predicate("action_read_only", t) is True


def test_one_unclassifiable_call_is_enough_to_lose_the_claim():
    """ALL of them, not most: "the run changed nothing" is unprovable while any
    single call in it is unknown."""
    t = trace(span("tool_call", "get_order"),
              span("tool_call", "frobnicate", i=1),
              span("llm_call", "llm", i=2))
    assert run_predicate("action_read_only", t) is False


def test_a_mutating_tool_is_still_binned_as_mutating():
    """The control the finding named: refusing to credit read_only must not make
    the coverpoint blind. `delete_account` is still the dangerous bin."""
    t = trace(span("tool_call", "delete_account"))
    assert run_predicate("action_read_only", t) is False
    assert run_predicate("action_mutating_irreversible", t) is True


def test_an_opaque_suite_lands_in_other_and_the_drift_is_reported():
    """Where an unclassifiable run GOES. `other` is labelled "unmodelled — a
    rising count is a finding", and `other_drift()` is what puts it in front of a
    reader — so the credit is not merely withdrawn, the gap is stated."""
    traces = [trace(span("tool_call", "mcp__acme__run"),
                    span("llm_call", "llm", i=1)).model_copy(
                        update={"trace_id": f"t{i}", "test_case_id": f"c{i}"})
              for i in range(5)]
    rep = collect(baseline_model(), [Sample(trace=t) for t in traces])
    risk = rep.coverpoints["action_risk"]
    assert risk.trace_closure == 0.0
    assert "read_only" in risk.unhit
    assert rep.other_drift()["action_risk"] == 1.0


def test_read_only_agrees_with_the_traffic_layers_own_refusal():
    """`verification/traffic.py` already refused this credit over ingested
    traffic and reported an "action_risk trustable %" beside closure. Batch and
    traffic must not disagree about the same span: a suite of opaque tool names is
    not better evidence than a stream of them."""
    from agenttic.verification.traffic import classify_confidence

    s = span("tool_call", "process_request")
    assert classify_confidence(s) == "unknown"
    assert run_predicate("action_read_only", trace(s)) is False


# --------------------------------------------------------------------------- #
# 2. escalated_to_human needs an OUTCOME, not a name
# --------------------------------------------------------------------------- #

class TestEscalationMustHaveHappened:
    def test_a_refused_escalation_is_not_an_escalation(self):
        """The path was attempted and BLOCKED, so nobody was handed anything.
        Crediting it says the recovery path was exercised when the run's real
        finding is that the agent cannot escalate at all."""
        t = trace(span("tool_call", "escalate_to_human",
                       error="permission denied"))
        assert run_predicate("traj_escalated_to_human", t) is False

    def test_a_failure_declared_without_text_is_also_refused(self):
        """Same span, failure declared as a status instead of a sentence."""
        t = trace(span("tool_call", "escalate_to_human",
                       attributes={"http.response.status_code": 403}))
        assert run_predicate("traj_escalated_to_human", t) is False

    @pytest.mark.parametrize("name", [
        "check_escalation_policy", "handoff_notes_lookup",
        "get_escalation_queue", "read_handoff_runbook"])
    def test_consulting_the_escalation_policy_is_not_escalating(self, name):
        """A READ about escalation is the agent doing its homework. `is_read` is
        the assertion layer's own classifier, so "consulted" and "performed" are
        separated by the same rule both layers already use."""
        t = trace(span("tool_call", name, output={"ok": True}))
        assert run_predicate("traj_escalated_to_human", t) is False

    def test_retrieving_an_escalation_article_is_not_escalating(self):
        t = trace(span("retrieval", "escalation_faq",
                       output={"text": "how to escalate a refund dispute"}))
        assert run_predicate("traj_escalated_to_human", t) is False

    def test_a_successful_handoff_still_credits_the_bin(self):
        t = trace(span("tool_call", "escalate_to_human",
                       output={"ticket": "T-1", "assignee": "human-queue"}))
        assert run_predicate("traj_escalated_to_human", t) is True

    def test_the_agents_own_decision_to_escalate_still_credits_the_bin(self):
        """The shape `tests/coverage/test_coverage_model.py` pins: an
        `agent_decision` span IS the act, and it did not fail."""
        t = trace(span("agent_decision", "escalate_to_human"))
        assert run_predicate("traj_escalated_to_human", t) is True

    def test_a_declared_escalation_attribute_wins_outright(self):
        """A producer that instruments the fact is the authority on it — and this
        is the arm that keeps working once escalation is a first-class span."""
        t = trace(span("tool_call", "notify_queue", attributes={"escalated": True}))
        assert run_predicate("traj_escalated_to_human", t) is True

    def test_a_handoff_named_tool_that_succeeded_credits_the_bin(self):
        t = trace(span("tool_call", "handoff_to_agent",
                       output={"accepted": True}))
        assert run_predicate("traj_escalated_to_human", t) is True

    def test_the_name_classifier_is_not_forked(self):
        """Hard rule: shared classifiers have exactly one implementation. The
        coverage layer adds an OUTCOME gate on top of `is_escalation`; it must
        not carry a second opinion about which names are escalations."""
        from agenttic.verification.builtins import is_escalation

        for name in ("escalate_to_human", "handoff_to_agent", "escalation_faq"):
            assert is_escalation(span("tool_call", name)) is True


# --------------------------------------------------------------------------- #
# 3. the phrase half of tool_condition
# --------------------------------------------------------------------------- #

#: (predicate, error text) — every one credited its bin before this change, and
#: every one is a message a real tool really sends.
PHRASE_FALSE_CREDITS = [
    # DIRECTION INVERTED. The bin means the tool's RESPONSE was malformed; both
    # of these say the AGENT's request was. Worse than a miss: the run did fail,
    # so a reader is shown a plausible cause pointing at the wrong side.
    ("tool_malformed_response", "Invalid JSON in request body"),
    ("tool_malformed_response", "schema mismatch: your payload is missing 'id'"),
    ("tool_malformed_response", "malformed request: you sent an unquoted key"),
    # "stale" modifying a CONNECTION is a dead socket, not out-of-date data. The
    # two are opposite findings: fix the transport vs. the agent trusted stale
    # records.
    ("tool_stale_data", "stale connection reset by peer"),
    ("tool_stale_data", "stale file handle"),
    # A config message about the ABSENCE of a rate limit, not a 429.
    ("tool_rate_limited", "rate limit not configured for this account"),
    ("tool_rate_limited", "rate limit is not set for tier free"),
    # A validation error about a parameter that happens to be called timeout.
    ("tool_timeout", "timeout must be a positive integer"),
    ("tool_timeout", "timeout should be between 1 and 30"),
]


@pytest.mark.parametrize("predicate_id,error_text", PHRASE_FALSE_CREDITS)
def test_mentioning_a_condition_is_not_reporting_it(predicate_id, error_text):
    t = trace(span("tool_call", "post_order", error=error_text))
    assert run_predicate(predicate_id, t) is False, error_text


@pytest.mark.parametrize("predicate_id,error_text", PHRASE_FALSE_CREDITS)
def test_the_failure_is_still_recorded_as_a_failure(predicate_id, error_text):
    """Refusing to guess the bin is not refusing to record the fault. Every one
    of these runs DID fail, and `tool_all_ok` must not claim otherwise."""
    t = trace(span("tool_call", "post_order", error=error_text))
    assert run_predicate("tool_all_ok", t) is False


#: The other direction. These are the reports the anchoring must NOT swallow —
#: over-tightening a coverpoint until nothing can hit it is the same defect
#: pointing the other way, and it is much harder to notice.
PHRASE_TRUE_CREDITS = [
    ("tool_timeout", "deadline exceeded"),
    ("tool_timeout", "the upstream call timed out after 30s"),
    ("tool_timeout", "request timeout waiting for the inventory service"),
    ("tool_timeout", "connection timeout"),
    ("tool_error_5xx", "502 Bad Gateway"),
    ("tool_error_5xx", "internal server error"),
    ("tool_error_5xx", "service unavailable, retry later"),
    ("tool_rate_limited", "rate limit exceeded, retry after 30s"),
    ("tool_rate_limited", "too many requests"),
    ("tool_stale_data", "the pricing cache is stale"),
    ("tool_stale_data", "returned an outdated inventory snapshot"),
    ("tool_stale_data", "served a cached copy from 6 hours ago"),
    ("tool_malformed_response", "the pricing api returned malformed json"),
    ("tool_malformed_response", "unparseable response body"),
    ("tool_malformed_response", "response failed schema mismatch check"),
    # The near-miss that deleted a veto clause. A draft `is (invalid|missing|
    # required)` disqualifier read "…is invalid json" as a validation complaint
    # and dropped a real report; found by reading the patterns back against
    # plausible messages, and kept here so it cannot come back.
    ("tool_malformed_response", "the response is invalid json"),
    ("tool_rate_limited", "the account is rate limited until 12:00"),
]


@pytest.mark.parametrize("predicate_id,error_text", PHRASE_TRUE_CREDITS)
def test_a_reported_condition_still_credits_its_bin(predicate_id, error_text):
    t = trace(span("tool_call", "get_order", error=error_text))
    assert run_predicate(predicate_id, t) is True, error_text


def test_an_incidental_mention_of_a_request_does_not_veto_a_real_report():
    """The disqualifier is scoped to the needle's NEIGHBOURHOOD, not to the whole
    message, precisely so this still credits: the tool's response was malformed,
    and the word "request" appears for an unrelated reason."""
    t = trace(span("tool_call", "get_price",
                   error="the pricing api returned malformed json for our request"))
    assert run_predicate("tool_malformed_response", t) is True


def test_one_disqualified_mention_does_not_silence_a_qualified_one():
    """Per-occurrence verdicts. A message can validate a parameter AND report the
    condition; the second sentence must still be heard."""
    t = trace(span("tool_call", "get_order",
                   error="timeout must be a positive integer; "
                         "meanwhile the call timed out after 30s"))
    assert run_predicate("tool_timeout", t) is True


def test_a_structured_status_still_credits_without_any_error_text():
    """The declared-status control, on a span whose failure carries NO prose —
    which is the shape an HTTP-instrumented producer emits."""
    t = trace(span("tool_call", "charge_card",
                   attributes={"http.response.status_code": 429}))
    assert run_predicate("tool_rate_limited", t) is True
    assert run_predicate("tool_all_ok", t) is False


# --------------------------------------------------------------------------- #
# 4. a failure declared without text is still a failure
# --------------------------------------------------------------------------- #

class TestASilentFailureIsStillAFailure:
    """`tool_all_ok` was credited to an ingested `charge_card` call that OTLP
    marked ERROR, because `_errored()` only recognised a failure that carried a
    SENTENCE. A predicate that needs the producer to be generous is not measuring
    the producer's system.
    """

    @pytest.mark.parametrize("attributes", [
        {"otel.status_code": "ERROR"},
        {"otel.status_code": "STATUS_CODE_ERROR"},
        {"otel_status_code": "error"},
        {"error.type": "DeadlineExceeded"},
        {"http.response.status_code": 503},
        {"http.status_code": 500},
        {"status_code": 429},
        {"response.status_code": 418},
    ])
    def test_a_declared_failure_with_no_message_is_not_all_ok(self, attributes):
        t = trace(span("tool_call", "charge_card", attributes=attributes))
        assert run_predicate("tool_all_ok", t) is False

    @pytest.mark.parametrize("attributes", [
        {"http.response.status_code": 200},
        {"http.status_code": 204},
        {"status_code": 302},
        {"otel.status_code": "OK"},
        {"otel.status_code": "UNSET"},
        {"error.type": ""},
    ])
    def test_a_declared_success_is_still_all_ok(self, attributes):
        """The other direction: 1xx-3xx and an explicit OK are not failures, and
        an empty `error.type` is the absence of a declaration."""
        t = trace(span("tool_call", "get_order", attributes=attributes))
        assert run_predicate("tool_all_ok", t) is True

    def test_a_declared_status_of_true_is_not_a_status_of_one(self):
        """`int(True) == 1` would make a bool look like a 1xx status."""
        t = trace(span("tool_call", "get_order",
                       attributes={"http.response.status_code": True}))
        assert run_predicate("tool_all_ok", t) is True

    def test_a_business_status_field_is_not_a_failure_declaration(self):
        """`output={"status": "shipped"}` is the tool answering the question it
        was asked. Only OTel's own status keys declare a span failure."""
        t = trace(span("tool_call", "get_order", output={"status": "shipped"}))
        assert run_predicate("tool_all_ok", t) is True

    def test_a_silent_failure_reaches_the_trajectory_axis_too(self):
        """`_errored` is shared, so recognising the failure also makes the retry
        and recovery bins see it — the fault stops being invisible everywhere at
        once, not just in `tool_all_ok`."""
        t = trace(span("tool_call", "get_order",
                       attributes={"otel.status_code": "ERROR"}),
                  span("tool_call", "get_order", i=1, output={"ok": True}),
                  span("llm_call", "llm", i=2))
        assert run_predicate("traj_retry_after_error", t) is True
        assert run_predicate("traj_recovered_from_tool_failure", t) is True

    def test_an_otlp_error_status_with_no_message_survives_ingest(self):
        """A producer that calls ``set_status(ERROR)`` with no description must
        still arrive as a failure.

        This was an ``xfail(strict=True)`` waiver: the predicate side was already
        correct, but ``map_span`` read ``status['message']`` and dropped the
        status itself, so the fact never reached the Trace and no predicate could
        recover it. ``strict=True`` was chosen so the waiver would go red the
        moment the ingest fix landed rather than sitting here rotting — and that
        is what happened. ``mapping.status_is_error`` now decides, and the
        machine-readable enum is preserved on ``otel.status_code``.
        """
        from agenttic.ingest.mapping import map_span
        from agenttic.ingest.otel import OtelSpan

        for status in ({"code": 2}, {"code": 2, "message": ""},
                       {"code": "STATUS_CODE_ERROR"}):
            mapped, _ = map_span(OtelSpan(
                trace_id="tr", span_id="sp", name="charge_card",
                start_ns=1_700_000_000_000_000_000,
                end_ns=1_700_000_001_000_000_000,
                attributes={"gen_ai.tool.name": "charge_card",
                            "gen_ai.operation.name": "execute_tool"},
                status=status))
            assert run_predicate("tool_all_ok", trace(mapped)) is False, status


# --------------------------------------------------------------------------- #
# 5. an injected fault may not borrow another fault's evidence
# --------------------------------------------------------------------------- #

class TestInjectedAttribution:
    """The loose edge Round 2 disclosed, narrowed as far as a trace allows.

    `injected_failures` is the scenario's record of what was configured around the
    agent, and it carries no tool identity (stimulus/realize.py:157 puts at most
    one condition in it and never says which call it applies to). So it cannot be
    matched to a span. What it CAN be stopped from doing is taking credit for a
    fault that already says what it was.
    """

    def test_an_injected_fault_may_not_take_credit_for_an_identified_one(self):
        """The tightening. The span reports a 5xx; an injected `rate_limited` is
        not evidence about it, and `error_5xx` is credited on its own evidence."""
        t = trace(span("tool_call", "get_order", error="503 service unavailable"),
                  span("llm_call", "llm", i=1))
        scenario = {"injected_failures": ["rate_limited"]}
        assert run_predicate("tool_rate_limited", t, scenario) is False
        assert run_predicate("tool_error_5xx", t, scenario) is True

    def test_a_corroborating_status_still_credits_the_injected_condition(self):
        """The asymmetry that makes the veto safe: a 504 reports `timeout` AND
        `error_5xx`, so an injected timeout corroborated by a 504 is credited."""
        t = trace(span("tool_call", "get_order",
                       attributes={"http.response.status_code": 504}))
        assert run_predicate("tool_timeout", t,
                             {"injected_failures": ["timeout"]}) is True

    def test_an_unidentified_failure_still_credits_the_injector(self):
        """Unchanged and deliberate: with no environment, the injector is the only
        authority on what it injected, and a message naming nothing cannot
        contradict it."""
        t = trace(span("tool_call", "get_order", error="call failed"),
                  span("llm_call", "llm", i=1))
        assert run_predicate("tool_timeout", t,
                             {"injected_failures": ["timeout"]}) is True

    def test_the_residual_looseness_is_documented_in_the_source(self):
        """DISCLOSED, not fixed: `error='order not found'` under an injected
        timeout still credits `timeout`. Closing it needs the fault injector to
        record WHICH CALL it failed (P4). A documented loose edge beats a silent
        wrong one — and this test is what keeps it documented, so the disclosure
        cannot be deleted while the behaviour stays.
        """
        from pathlib import Path

        t = trace(span("tool_call", "get_order", error="order not found"))
        assert run_predicate("tool_timeout", t,
                             {"injected_failures": ["timeout"]}) is True

        body = (Path(__file__).resolve().parents[2]
                / "src/agenttic/coverage/extractors.py").read_text()
        doc = body[body.index("def _condition_signal"):]
        doc = doc[:doc.index('"""', doc.index('"""') + 3)]
        assert "order not found" in doc, (
            "the residual mis-attribution must be named in the docstring of the "
            "function that performs it")


# --------------------------------------------------------------------------- #
# 6. the direction of the whole round
# --------------------------------------------------------------------------- #

def _suite(tool_name, *, error=None, attributes=None, n=5):
    out = []
    for i in range(n):
        t = trace(span("llm_call", "llm"),
                  span("tool_call", tool_name, i=1, error=error,
                       attributes=attributes),
                  span("final_output", "final", i=2))
        out.append(t.model_copy(update={"trace_id": f"t{i}",
                                        "test_case_id": f"c{i}"}))
    return out


def test_closure_falls_when_a_credit_was_never_earned():
    """The headline moves DOWN, and that is the point of the round.

    Pinned as an inequality against a suite that IS entitled to the credit rather
    than as a literal, so it keeps meaning the same thing when the model version
    changes the denominator.
    """
    earned = collect(baseline_model(),
                     [Sample(trace=t) for t in _suite("get_order")])
    unearned = collect(baseline_model(),
                       [Sample(trace=t) for t in _suite("mcp__acme__run")])
    assert unearned.trace_closure < earned.trace_closure
    assert "read_only" in unearned.coverpoints["action_risk"].unhit
    assert "read_only" not in earned.coverpoints["action_risk"].unhit


def test_a_suite_of_phrase_collisions_no_longer_closes_four_bins():
    """Five runs whose tools failed for five unrelated reasons used to report
    `tool_condition` at 4-of-6 bins hit — closure 0.667 on a suite that never met
    a timeout, a rate limit, stale data or a malformed response."""
    traces = []
    for i, msg in enumerate(["Invalid JSON in request body",
                             "stale connection reset by peer",
                             "rate limit not configured for this account",
                             "timeout must be a positive integer",
                             "schema mismatch: your payload is missing 'id'"]):
        t = trace(span("llm_call", "llm"),
                  span("tool_call", "post_order", i=1, error=msg),
                  span("final_output", "final", i=2))
        traces.append(t.model_copy(update={"trace_id": f"t{i}",
                                           "test_case_id": f"c{i}"}))
    rep = collect(baseline_model(), [Sample(trace=t) for t in traces])
    tools = rep.coverpoints["tool_condition"]
    for never in ("timeout", "rate_limited", "stale_data", "malformed_response"):
        assert never in tools.unhit, never
    # and the faults are not swallowed: `all_ok` is unhit too, so the reader is
    # told the calls failed AND that we will not name the cause.
    assert "all_ok" in tools.unhit
    assert tools.trace_closure == 0.0
