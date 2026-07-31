"""The envelope leg must not read ``populated`` on a latency nobody measured.

``build_signoff``'s own docstring states the contract: *"Any leg whose artifact
is absent stays not_run — it never silently reads as a pass."* Every other leg
honours it. The envelope did not: the guard was

    if getattr(scorecard, "p95_latency_ms", None) is not None:
        s.envelope.status = "populated"

and ``p95_latency_ms`` is a ``float`` field defaulting to ``0.0``, so the guard
was true for every scorecard ever built. A suite whose runs all died in the
harness — ``harness/runner.py`` synthesizes those traces with
``total_latency_ms=0.0`` — produced ``envelope.status == "populated"`` and
``p95 0ms``, which ``reporting/signoff_report.py`` then printed as a measured
performance envelope.

Zero milliseconds is not a latency. It is the absence of one, and this repo has
a name for reporting an absence as a measurement.

The envelope is not a sign-off gate (``signs_off`` binds on coverage,
assertions and formal only), so nothing here changes a verdict. It changes what
the signed document *claims to have measured*, which is the part an auditor
reads.
"""

from agenttic.schema.scorecard import RunScore, Scorecard
from agenttic.schema.signoff import build_signoff


def _scorecard(latencies: list[float]) -> Scorecard:
    """A scorecard whose runs have the given per-run latencies."""
    return Scorecard(
        scorecard_id="sc-1", agent_id="a", suite_id="s", suite_version=1,
        rubric_id="r", rubric_version=1,
        run_scores=[
            RunScore(trace_id=f"t{i}", test_id=f"c{i}", criterion_scores=[],
                     passed=True, latency_ms=ms)
            for i, ms in enumerate(latencies)
        ],
        task_success_rate=1.0, mean_cost_usd=0.0, total_cost_usd=0.0,
        total_scoring_cost_usd=0.0,
        p95_latency_ms=(sorted(latencies)[-1] if latencies else 0.0),
        per_criterion_means={}, visibility_tier="glass_box",
    )


def test_an_unmeasured_latency_does_not_populate_the_envelope():
    """Every run reported 0.0 ms — nothing takes zero milliseconds, so nothing
    was measured. The leg must stay ``not_run``."""
    s = build_signoff(signoff_id="so-1", agent_id="a",
                      scorecard=_scorecard([0.0, 0.0, 0.0]))
    assert s.envelope.status == "not_run"
    assert "envelope" in s.missing_legs()


def test_a_measured_latency_still_populates_the_envelope():
    """The guard must not cost us the real case."""
    s = build_signoff(signoff_id="so-2", agent_id="a",
                      scorecard=_scorecard([120.0, 340.0, 1900.0]))
    assert s.envelope.status == "populated"
    assert s.envelope.p95_latency_ms == 1900.0


def test_one_measured_run_among_zeros_is_still_a_measurement():
    """A partially-instrumented suite has measured *something*. The claim is
    'this number came from a clock', not 'every run was timed' — the run count
    is already carried elsewhere, and downgrading a real measurement to not_run
    would be the opposite error."""
    s = build_signoff(signoff_id="so-3", agent_id="a",
                      scorecard=_scorecard([0.0, 0.0, 55.0]))
    assert s.envelope.status == "populated"


def test_a_scorecard_with_no_runs_cannot_populate_the_envelope():
    s = build_signoff(signoff_id="so-4", agent_id="a", scorecard=_scorecard([]))
    assert s.envelope.status == "not_run"


def test_the_envelope_is_not_a_signoff_gate():
    """Pinned deliberately: this change must not move a verdict. ``signs_off``
    binds on coverage, assertions and formal. If a future change makes the
    envelope a gate, this test fails and the author has to say so out loud."""
    measured = build_signoff(signoff_id="so-5", agent_id="a",
                             scorecard=_scorecard([500.0]))
    unmeasured = build_signoff(signoff_id="so-6", agent_id="a",
                               scorecard=_scorecard([0.0]))
    assert measured.signs_off == unmeasured.signs_off
