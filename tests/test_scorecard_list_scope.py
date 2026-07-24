"""Every scorecard list row carries the SCOPE of its pass rate.

SPEC-13 reframed the run view to lead with coverage closure and assertions. A
list that ships only `task_success_rate` quietly undoes that: the reader sees a
percentage with nothing beside it saying how much it is claiming. So
`list_scorecards` attaches a compact verification summary to every row, and the
console renders the rate through it.
"""

from __future__ import annotations

from agenttic.server.store import _coverage_summary

FULL_COVERAGE = {
    "model_ref": "coverage:baseline@v1",
    "bins_fingerprint": "abc123",
    "baseline": True,
    "limits": "Baseline coverage model only.",
    "trace_closure": 0.42,
    "closure_target": 0.95,
    "closed": False,
    # the heavy detail that must NOT travel on a list row
    "per_coverpoint": {"trajectory": {"closure": 0.5, "unhit": ["x"], "other_hits": 0}},
    "crosses": {"trajectory_x_tool": 0.3},
    "holes": [{"kind": "bin", "where": "trajectory", "what": "x"}],
    "other_drift": 0.0,
    "assertions": {
        "total": 8, "violations": 1, "unexercised": 3, "verdict": "FAIL",
        "exercised_ratio": 0.625,
        "violated_properties": [{"assertion_id": "a1", "severity": "high",
                                 "detail": "…", "traces": "1/4 runs"}],
        "unexercised_properties": ["a5", "a6", "a7"],
    },
}


def test_summary_carries_the_scope_fields_the_console_renders():
    out = _coverage_summary({"coverage": FULL_COVERAGE})
    assert out["model_ref"] == "coverage:baseline@v1"
    assert out["baseline"] is True
    assert out["trace_closure"] == 0.42
    assert out["closure_target"] == 0.95
    assert out["closed"] is False
    assert out["limits"] == "Baseline coverage model only."
    assert out["assertions"] == {
        "total": 8, "violations": 1, "unexercised": 3, "verdict": "FAIL"}


def test_summary_leaves_the_heavy_detail_on_the_scorecard():
    """A list row is fetched for every result on the dashboard; the per-coverpoint
    breakdown belongs to the scorecard endpoint, not to every row."""
    out = _coverage_summary({"coverage": FULL_COVERAGE})
    for heavy in ("per_coverpoint", "crosses", "holes", "bins_fingerprint"):
        assert heavy not in out
    assert "violated_properties" not in out["assertions"]
    assert "unexercised_properties" not in out["assertions"]


def test_a_scorecard_with_no_coverage_summarises_to_empty():
    """Absent, not fabricated — the UI renders 'not measured' from this, and an
    empty dict is falsy so `hasVerification` reports honestly."""
    assert _coverage_summary({}) == {}
    assert _coverage_summary({"coverage": {}}) == {}


def test_coverage_without_assertions_still_reports_closure():
    out = _coverage_summary({"coverage": {"model_ref": "m", "trace_closure": 0.9,
                                          "closed": True}})
    assert out["trace_closure"] == 0.9
    assert "assertions" not in out


def test_assertions_without_a_coverage_model_still_travel():
    """Assertions run even when no coverage model applied — the row must be able
    to say the properties held while the rate stays tagged unscoped."""
    out = _coverage_summary({"coverage": {
        "assertions": {"total": 8, "violations": 0, "unexercised": 0,
                       "verdict": "PASS"}}})
    assert out["model_ref"] is None
    assert out["assertions"]["verdict"] == "PASS"
