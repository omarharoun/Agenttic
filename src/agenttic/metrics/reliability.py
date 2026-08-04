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


def pass_at_k(per_case_results: list[list[bool]]) -> float:
    """Fraction of cases that passed at least ONCE across their k runs.

    The complement of ``pass_hat_k``, and the two answer different questions —
    both eval sources define both, deliberately:

    * ``pass^k`` — "reliable EVERY time". The bar for a customer-facing agent,
      and the one a single-shot leaderboard hides. It FALLS as k rises.
    * ``pass@k`` — "one success is enough". The bar when a human reviews the
      output before it counts: a coding agent whose patch you read, a draft you
      edit. It RISES as k rises.

    Reporting only pass^k understates an agent whose product is a candidate, and
    reporting only pass@k overstates one that must be right unattended. Neither
    is the honest single number, which is why both are reported.

    Cases with no runs are excluded, exactly as the other two do — a case that
    never executed is absent evidence, not a failure.
    """
    cases = [r for r in per_case_results if r]
    if not cases:
        return 0.0
    return sum(1 for r in cases if any(r)) / len(cases)


def flakiness(per_case_results: list[list[bool]]) -> float:
    """Fraction of cases that passed at least once AND failed at least once.

    The gap between pass@k and pass^k, per case. A case here is not "hard" — it
    is NON-DETERMINISTIC, and that is a different finding with a different fix:
    a hard case needs a better agent, a flaky one needs a cause.
    """
    cases = [r for r in per_case_results if r]
    if not cases:
        return 0.0
    return sum(1 for r in cases if any(r) and not all(r)) / len(cases)
