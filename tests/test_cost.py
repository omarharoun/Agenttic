"""Pre-run cost estimation."""

from ascore.cost import estimate_run_cost, judge_model_for

CFG = {
    "models": {"agent_default": "claude-sonnet-4-6",
               "judge_executor": "claude-sonnet-4-6",
               "judge_strong": "claude-opus-4-8"},
    "pricing": {"claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
                "claude-opus-4-8": {"input": 15.0, "output": 75.0},
                "default": {"input": 3.0, "output": 15.0}},
    "cost": {"expected_agent_steps": 2, "expected_input_tokens": 1000,
             "expected_output_tokens": 200, "judge_input_tokens": 1000,
             "judge_output_tokens": 100},
}


class TestEstimateMath:
    def test_reference_agent_plus_judge(self):
        est = estimate_run_cost(
            CFG, n_cases=10, agent_variant="reference",
            agent_model="claude-sonnet-4-6", n_judge_criteria=2,
            judge_model="claude-opus-4-8")
        # agent: 2 calls * (1000*3 + 200*15)/1e6 = 2 * 0.006 = 0.012 per case * 10
        assert est.projected_agent_usd == round(0.012 * 10, 6)
        # judge: 2 criteria * (1000*15 + 100*75)/1e6 = 2 * 0.0225 = 0.045 * 10
        assert est.projected_judge_usd == round(0.045 * 10, 6)
        assert est.projected_usd == round(est.projected_agent_usd
                                          + est.projected_judge_usd, 6)

    def test_blackbox_agent_cost_unknown(self):
        est = estimate_run_cost(
            CFG, n_cases=5, agent_variant="blackbox", agent_model=None,
            n_judge_criteria=1, judge_model="claude-opus-4-8")
        assert est.projected_agent_usd == 0.0
        assert any("black-box" in n for n in est.notes)
        assert est.projected_judge_usd > 0  # judge still estimated

    def test_scales_with_case_count(self):
        a = estimate_run_cost(CFG, n_cases=1, agent_variant="reference",
                              agent_model="claude-sonnet-4-6")
        b = estimate_run_cost(CFG, n_cases=10, agent_variant="reference",
                              agent_model="claude-sonnet-4-6")
        assert round(b.projected_usd, 6) == round(a.projected_usd * 10, 6)


def test_judge_model_tiering():
    # executor differs from agent -> tiered executor is the judge model
    assert judge_model_for(CFG, "some-other-agent") == "claude-sonnet-4-6"
    # agent == executor -> fall back to strong
    assert judge_model_for(CFG, "claude-sonnet-4-6") == "claude-opus-4-8"
