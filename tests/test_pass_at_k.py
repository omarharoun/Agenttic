"""pass@k beside pass^k — they answer different questions, so both are reported.

`metrics/reliability.py` had only `case_passes_k`, `pass_hat_k` and `pass_at_1`;
there was no any-of-k anywhere in the repo. Both eval sources define BOTH
deliberately:

* **pass^k** — "reliable EVERY time". The bar for an agent acting unattended,
  and the thing a single-shot leaderboard hides. Falls as k rises.
* **pass@k** — "one success is enough". The bar when a human reviews the output
  before it counts — a coding agent's patch you read, a draft you edit. Rises
  as k rises.

Reporting only pass^k understates an agent whose product is a candidate;
reporting only pass@k overstates one that must be right unattended. Neither is
the honest single number.

Both are TOP-LEVEL result keys, never `components`: `compute_index` intersects
metric values with `index_weights()`, so an unweighted key provably cannot move
the Index. That property is pinned below rather than assumed.
"""

from __future__ import annotations

import pytest

from agenttic.metrics.reliability import (case_passes_k, flakiness, pass_at_1,
                                          pass_at_k, pass_hat_k)


class TestTheTwoQuestions:
    def test_they_diverge_on_a_flaky_agent(self):
        """The whole reason both exist. Same runs, opposite readings."""
        runs = [[True, True, True], [True, False, True],
                [False, False, False], [True, True, False]]
        assert pass_at_k(runs) == 0.75      # three cases succeeded at least once
        assert pass_hat_k(runs) == 0.25     # one case succeeded every time

    def test_they_agree_on_a_deterministic_agent(self):
        """With no flakiness there is nothing between them — which is why a
        divergence is a measurement of non-determinism, not of difficulty."""
        runs = [[True, True], [False, False], [True, True]]
        assert pass_at_k(runs) == pass_hat_k(runs)

    def test_pass_at_k_rises_and_pass_hat_k_falls_with_more_attempts(self):
        one = [[True], [False]]
        three = [[True, False, False], [False, True, False]]
        assert pass_at_k(three) > pass_at_k(one)
        assert pass_hat_k(three) < pass_hat_k(one)

    def test_a_single_success_is_enough_for_pass_at_k(self):
        assert pass_at_k([[False, False, True]]) == 1.0
        assert pass_hat_k([[False, False, True]]) == 0.0


class TestFlakiness:
    def test_it_is_the_gap_between_the_two(self):
        runs = [[True, True], [True, False], [False, False], [False, True]]
        assert flakiness(runs) == 0.5
        assert flakiness(runs) == pytest.approx(pass_at_k(runs) - pass_hat_k(runs))

    def test_a_case_that_always_fails_is_hard_not_flaky(self):
        """Different finding, different fix: a hard case needs a better agent,
        a flaky one needs a cause."""
        assert flakiness([[False, False, False]]) == 0.0

    def test_a_case_that_always_passes_is_not_flaky(self):
        assert flakiness([[True, True, True]]) == 0.0


class TestAbsentEvidence:
    @pytest.mark.parametrize("fn", [pass_at_k, pass_hat_k, pass_at_1, flakiness])
    def test_no_cases_is_zero_not_a_crash(self, fn):
        assert fn([]) == 0.0

    @pytest.mark.parametrize("fn", [pass_at_k, pass_hat_k, flakiness])
    def test_a_case_that_never_RAN_is_excluded_not_failed(self, fn):
        """`run_standard` skips errored runs, so a case can arrive with an empty
        vector. Absent evidence is not a failure — counting it as one would let
        an infrastructure outage read as an unreliable agent."""
        assert fn([[True, True], []]) == fn([[True, True]])


class TestItCannotMoveTheIndex:
    def test_the_new_keys_are_not_index_components(self):
        """`compute_index` keeps only metric ids present in `index_weights()`,
        so a top-level result key cannot reach it. Pinned because the whole
        safety of this addition rests on it."""
        from agenttic.metrics.catalog import index_weights
        from agenttic.metrics.index import compute_index

        weights = index_weights()
        assert "pass_at_k" not in weights
        assert "flaky_rate" not in weights
        base = compute_index({"reliability_pass_k": 1.0})
        with_new = compute_index({"reliability_pass_k": 1.0,
                                  "pass_at_k": 0.0, "flaky_rate": 1.0})
        assert base["index"] == with_new["index"]
        assert base["missing"] == with_new["missing"]

    def test_the_runner_reports_them_at_top_level(self):
        import inspect

        from agenttic.metrics import runner

        src = inspect.getsource(runner.run_standard)
        assert '"pass_at_k"' in src and '"flaky_rate"' in src
        assert 'components["pass_at_k"]' not in src
