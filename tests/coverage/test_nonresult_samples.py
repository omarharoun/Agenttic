"""A run that never happened must not manufacture coverage (rescue P0/R2).

The harness synthesizes a Trace when the adapter cannot complete a run — a
transport failure, a timeout, a budget kill. It has one ``error`` span, zero tool
calls, and ``final_output`` set to a marker like ``HARNESS_FAILURE:transport``.
``scoring.engine.nonresult_reason`` already refuses to score those; the coverage
path did not, so the marker string read as an answer (``traj_direct_answer``) and
the error text read as environment content (``data_condition``: a dead endpoint's
"404 Not Found" credited `entity_not_found`).

These tests are built on the REAL synthesizer (``harness.runner._failure_trace``),
not a lookalike fixture, so they fail if the marker format ever changes. The
second half is the guard in the other direction: a trace with a genuine tool
error that the agent recovered from is a REAL result and must keep every bin it
earns — the distinction is whether the agent ran, not whether something errored.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from agenttic.coverage.collect import Sample, collect, nonresult_marker
from agenttic.coverage.models.baseline import baseline_model
from agenttic.harness.runner import _failure_trace
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

NOW = datetime.now(timezone.utc)


class _Adapter:
    """The three attributes ``_failure_trace`` reads."""

    agent_id = "agent-under-test"
    visibility = "glass_box"

    def config_hash(self) -> str:
        return "cfg-hash"


class _Case:
    test_id = "case-1"


def harness_failure(kind: str = "transport", detail: str | None = None) -> Trace:
    """A real harness non-result. The default detail is the one measured in the
    wild: a transport error whose text contains "404 Not Found", which is what
    credited `data_entity_not_found` to a run that never reached a tool."""
    return _failure_trace(
        _Adapter(), _Case(), kind,
        detail or "HTTPError: 404 Not Found for url https://agent.acme.io/v1/chat")


def _span(kind: str, name: str, **kw) -> Span:
    return Span(span_id=uuid.uuid4().hex[:12], kind=kind, name=name,
                start_time=NOW, end_time=NOW, **kw)


def recovered_run() -> Trace:
    """A REAL result: the agent called a tool, the tool 503'd, the agent called
    it again successfully and answered. Every bin this earns is evidence that the
    thing happened."""
    return Trace(
        trace_id=uuid.uuid4().hex, agent_id="agent-under-test",
        agent_config_hash="cfg-hash", test_case_id="case-2",
        spans=[
            _span("llm_call", "plan"),
            _span("tool_call", "lookup_order", error="HTTP 503 service unavailable",
                  attributes={"http.response.status_code": 503}),
            _span("llm_call", "retry"),
            _span("tool_call", "lookup_order", output={"order_id": "A-1", "status": "shipped"}),
        ],
        visibility="glass_box", final_output="Your order A-1 has shipped.",
        total_cost_usd=0.01, total_latency_ms=120.0, total_steps=2,
        schema_version=SCHEMA_VERSION)


def _hits(report, coverpoint_id: str) -> list[str]:
    cp = report.coverpoints[coverpoint_id]
    return sorted(b.bin_id for b in cp.bins.values() if b.hit)


# --------------------------------------------------------------------------- #
# a non-result credits nothing
# --------------------------------------------------------------------------- #

class TestNonResultCreditsNoBin:
    @pytest.mark.parametrize(
        "kind", ["transport", "timeout", "harness_error", "budget_exceeded"])
    def test_no_bin_is_credited_on_any_harness_failure(self, kind):
        report = collect(baseline_model(closure_target=0.95),
                         [Sample(trace=harness_failure(kind))])
        for cp_id in report.coverpoints:
            assert _hits(report, cp_id) == [], (kind, cp_id)

    def test_the_two_measured_over_reports_are_gone(self):
        """The exact bins the rescue measured, named so a regression is legible.

        ``traj_direct_answer`` is "no tools AND non-empty final_output", and
        "HARNESS_FAILURE:transport" is non-empty. ``data_entity_not_found``
        substring-matches "not found" over tool AND error spans, and the
        transport error said "404 Not Found".
        """
        report = collect(baseline_model(closure_target=0.95),
                         [Sample(trace=harness_failure())])
        assert "direct_answer" not in _hits(report, "trajectory")
        assert "entity_not_found" not in _hits(report, "data_condition")

    def test_headline_closure_is_zero_when_nothing_ran(self):
        report = collect(baseline_model(closure_target=0.95),
                         [Sample(trace=harness_failure())])
        assert report.trace_closure == 0.0
        assert report.closed is False

    def test_the_other_bins_are_not_credited_either(self):
        """A non-result must not land in `other` — ``other_drift`` is read as
        "the model is missing a dimension", and a run that never happened is not
        evidence of a missing dimension."""
        report = collect(baseline_model(closure_target=0.95),
                         [Sample(trace=harness_failure())])
        assert report.other_drift() == {}
        assert all(cp.other_hits == 0 for cp in report.coverpoints.values())

    def test_every_bin_stays_an_honest_hole_and_nothing_is_illegal(self):
        """The gate removes credit, not disclosure. A non-result cannot trip an
        illegal bin (it exhibited nothing), and every real bin remains a hole —
        which is what a run that never happened actually leaves behind."""
        report = collect(baseline_model(closure_target=0.95),
                         [Sample(trace=harness_failure())])
        assert report.illegal_hits == []
        assert report.holes()
        assert "trajectory" in {h.where for h in report.holes()}
        assert "direct_answer" in {h.what for h in report.holes()}


# --------------------------------------------------------------------------- #
# ... but the exclusion is never silent
# --------------------------------------------------------------------------- #

class TestTheExclusionIsReported:
    def test_counted_out_of_the_measured_denominator_and_into_the_tally(self):
        report = collect(
            baseline_model(closure_target=0.95),
            [Sample(trace=recovered_run()), Sample(trace=harness_failure()),
             Sample(trace=harness_failure("timeout", "run exceeded 30s"))])
        assert report.n_samples == 1          # what was measured
        assert report.n_nonresults == 2       # what was not
        assert report.n_submitted == 3        # and the two always add up

    def test_the_tally_names_what_failed(self):
        report = collect(
            baseline_model(closure_target=0.95),
            [Sample(trace=harness_failure("timeout", "run exceeded 30s")),
             Sample(trace=harness_failure("timeout", "run exceeded 30s")),
             Sample(trace=harness_failure("transport", "ConnectionError: refused"))])
        assert report.nonresult_reasons == {
            "HARNESS_FAILURE:timeout": 2, "HARNESS_FAILURE:transport": 1}

    def test_as_dict_carries_all_three_numbers(self):
        """``as_dict`` is what artifacts are built from. A closure figure whose
        denominator is undisclosed is the over-report wearing a different hat."""
        d = collect(baseline_model(closure_target=0.95),
                    [Sample(trace=recovered_run()),
                     Sample(trace=harness_failure())]).as_dict()
        assert d["samples"] == 1
        assert d["samples_submitted"] == 2
        assert d["non_results"] == 1
        assert d["non_result_reasons"] == {"HARNESS_FAILURE:transport": 1}

    def test_the_headline_string_says_it_in_words(self):
        head = collect(baseline_model(closure_target=0.95),
                       [Sample(trace=recovered_run()),
                        Sample(trace=harness_failure())]).headline()
        assert "NOT MEASURED" in head
        assert "1 of 2 submitted" in head
        assert "HARNESS_FAILURE:transport" in head

    def test_a_clean_batch_says_nothing_about_non_results(self):
        report = collect(baseline_model(closure_target=0.95),
                         [Sample(trace=recovered_run())])
        assert report.n_nonresults == 0
        assert report.nonresult_reasons == {}
        assert "NOT MEASURED" not in report.headline()
        assert report.as_dict()["non_results"] == 0


# --------------------------------------------------------------------------- #
# a real run that errored is NOT a non-result
# --------------------------------------------------------------------------- #

class TestARealRunKeepsItsBins:
    def test_recovered_from_tool_failure_still_credits_its_bins(self):
        """The agent ran. A tool failed. It recovered and answered. This is
        exactly the trajectory the coverpoint exists to measure, and the gate
        must not touch it — the distinction is whether the AGENT RAN, not whether
        something errored."""
        report = collect(baseline_model(closure_target=0.95),
                         [Sample(trace=recovered_run())])
        assert report.n_nonresults == 0
        assert report.n_samples == 1
        traj = _hits(report, "trajectory")
        assert "recovered_from_tool_failure" in traj
        assert "retry_after_error" in traj
        assert "multi_tool_chain" in traj
        # and the environment fault it really met
        assert "error_5xx" in _hits(report, "tool_condition")

    def test_mixing_in_non_results_does_not_move_a_single_number(self):
        """The headline over 1 real run and over the same run plus two dead ones
        must be identical. Anything else means the dead runs contributed."""
        real = [Sample(trace=recovered_run())]
        clean = collect(baseline_model(closure_target=0.95), real)
        polluted = collect(
            baseline_model(closure_target=0.95),
            real + [Sample(trace=harness_failure()),
                    Sample(trace=harness_failure("timeout", "run exceeded 30s"))])
        assert polluted.trace_closure == clean.trace_closure
        assert polluted.other_drift() == clean.other_drift()
        assert ({cp: c.trace_closure for cp, c in polluted.coverpoints.items()}
                == {cp: c.trace_closure for cp, c in clean.coverpoints.items()})
        assert polluted.holes() == clean.holes()


class TestTheMarkerItself:
    def test_every_execution_failure_prefix_is_recognised(self):
        """Read from the scoring engine, so the two subsystems cannot disagree
        about which runs are non-results."""
        from agenttic.scoring.engine import EXECUTION_FAILURE_PREFIXES

        for prefix in EXECUTION_FAILURE_PREFIXES:
            t = recovered_run().model_copy(
                update={"final_output": f"{prefix}:SomeError: detail here"})
            assert nonresult_marker(t) == f"{prefix}:SomeError"

    def test_a_real_answer_is_never_a_marker(self):
        assert nonresult_marker(recovered_run()) is None

    def test_an_empty_answer_is_not_a_non_result(self):
        """An agent that returned nothing DID run; its silence is a result, and
        deciding otherwise here would throw away real behaviour."""
        t = recovered_run().model_copy(update={"final_output": ""})
        assert nonresult_marker(t) is None

    def test_the_label_drops_the_message_tail(self):
        """The tally reaches a report, and the tail can carry a URL or a payload
        fragment. It is also what would explode the tally into one bucket per
        message."""
        t = harness_failure(
            "transport",
            "HTTPError: 404 Not Found for url https://agent.acme.io/v1/chat")
        assert nonresult_marker(t) == "HARNESS_FAILURE:transport"
        assert "acme.io" not in (nonresult_marker(t) or "")
