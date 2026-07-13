"""The A/B significance math, tested on known inputs — a clear win and a
no-difference case for each test, plus the small-sample (underpowered) guard.
These are the load-bearing claims of the whole feature, so they're tested in
isolation from the run machinery."""

from agenttic.stats import McNemarResult, mcnemar, paired_bootstrap


class TestMcNemar:
    def test_clear_win_is_significant(self):
        # 20 cases: A fails all, B passes all -> 20 discordant, all favoring B.
        a = [False] * 20
        b = [True] * 20
        r = mcnemar(a, b)
        assert r.b == 0 and r.c == 20
        assert r.significant
        assert r.p_value < 0.001
        assert r.favors == "B"

    def test_no_difference_when_concordant(self):
        # identical outcomes -> zero discordant pairs -> not significant.
        a = [True, False, True, True, False, True]
        r = mcnemar(a, list(a))
        assert r.n_discordant == 0
        assert not r.significant
        assert r.p_value == 1.0
        assert r.underpowered  # no information at all

    def test_no_difference_when_balanced_discordant(self):
        # equal flips both ways -> no evidence either variant is better.
        a = [True, True, True, True, False, False, False, False] * 3
        b = [False, False, False, False, True, True, True, True] * 3
        r = mcnemar(a, b)
        assert r.b == r.c == 12
        assert not r.significant
        assert r.p_value > 0.5

    def test_small_sample_underpowered(self):
        # 4 discordant pairs all one way: real signal, but too few to conclude.
        a = [False, False, False, False]
        b = [True, True, True, True]
        r = mcnemar(a, b)
        assert r.n_discordant == 4
        assert r.underpowered          # 2*0.5^4 = 0.125 > 0.05, can't reach sig
        assert not r.significant

    def test_six_discordant_can_be_significant(self):
        # exactly the threshold where a clean split becomes significant.
        r = mcnemar([False] * 6, [True] * 6)
        assert not r.underpowered      # 2*0.5^6 = 0.03125 <= 0.05
        assert r.significant
        assert r.test == "exact"

    def test_chi2_path_for_large_samples(self):
        # >= 25 discordant -> continuity-corrected chi-square branch.
        a = [False] * 40 + [True] * 5
        b = [True] * 40 + [False] * 5
        r = mcnemar(a, b)
        assert r.test == "chi2_cc"
        assert r.significant


class TestPairedBootstrap:
    def test_clear_shift_is_significant(self):
        # B consistently scores a full point above A on every case.
        a = [0.0] * 30
        b = [1.0] * 30
        r = paired_bootstrap(a, b)
        assert r.delta == 1.0
        assert r.direction == "B"
        assert r.significant
        assert r.ci_low > 0  # CI excludes zero

    def test_no_difference_identical(self):
        a = [1.0, 0.5, 0.0, 1.0, 0.5, 1.0, 0.0, 0.5]
        r = paired_bootstrap(a, list(a))
        assert r.delta == 0.0
        assert r.direction == "tie"
        assert not r.significant
        assert r.p_value == 1.0

    def test_noisy_no_difference_not_significant(self):
        # same values, just reordered -> means equal, differences average ~0.
        a = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
        b = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        r = paired_bootstrap(a, b)
        assert abs(r.delta) < 1e-9
        assert not r.significant

    def test_deterministic_for_seed(self):
        a = [0.0, 0.5, 1.0, 0.5, 0.0, 1.0, 0.5, 0.0, 1.0, 0.5]
        b = [0.5, 1.0, 1.0, 1.0, 0.5, 1.0, 1.0, 0.5, 1.0, 1.0]
        r1 = paired_bootstrap(a, b)
        r2 = paired_bootstrap(a, b)
        assert r1.p_value == r2.p_value
        assert r1.ci_low == r2.ci_low and r1.ci_high == r2.ci_high

    def test_direction_favors_a_when_b_worse(self):
        a = [1.0] * 20
        b = [0.0] * 20
        r = paired_bootstrap(a, b)
        assert r.delta == -1.0
        assert r.direction == "A"
        assert r.significant
