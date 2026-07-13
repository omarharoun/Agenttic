"""Reliability — pass^k (tau-bench-style).

Run the same case k independent times; the case is reliable only if it succeeds
on ALL k runs. ``pass_hat_k`` is the fraction of cases that pass^k. This is the
'works once, flaky in prod' signal that single-run pass@1 hides.
"""

from __future__ import annotations


def case_passes_k(results: list[bool]) -> bool:
    """A case passes^k iff it passed on every one of its k runs (and k >= 1)."""
    return len(results) >= 1 and all(results)


def pass_hat_k(per_case_results: list[list[bool]]) -> float:
    """Fraction of cases that pass on all k runs. ``per_case_results[i]`` is the
    list of k boolean pass/fail outcomes for case i."""
    cases = [r for r in per_case_results if r]  # ignore cases with no runs
    if not cases:
        return 0.0
    return sum(1 for r in cases if case_passes_k(r)) / len(cases)


def pass_at_1(per_case_results: list[list[bool]]) -> float:
    """Standard single-run pass rate (first run), for contrast with pass^k."""
    cases = [r for r in per_case_results if r]
    if not cases:
        return 0.0
    return sum(1 for r in cases if r[0]) / len(cases)
