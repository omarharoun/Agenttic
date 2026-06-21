"""A/B report rendering — Markdown is client-presentable (verdict, paired
success, per-criterion deltas, flipped cases, cost) and the PDF renders to
valid bytes."""

import re

from ascore.reporting.ab_report import render_ab_markdown, render_ab_pdf
from ascore.schema.ab import (
    ABComparison,
    ABVariant,
    CriterionComparison,
    FlippedCase,
)


def _comparison(winner="B", significant=True):
    return ABComparison(
        comparison_id="cmp-1", suite_id="support-v1", suite_version=1,
        rubric_id="r-1", rubric_version=1,
        label_a="A", label_b="B",
        variant_a=ABVariant(label="A", agent_id="router", model="haiku"),
        variant_b=ABVariant(label="B", agent_id="router", model="sonnet"),
        scorecard_a_id="sc-a", scorecard_b_id="sc-b",
        n_paired=12, excluded_test_ids=["tc-err"],
        success_rate_a=0.5, success_rate_b=0.83, success_delta=0.33,
        mcnemar={"b": 1, "c": 6, "n_discordant": 7, "statistic": 0.0,
                 "p_value": 0.0156 if significant else 0.4, "test": "exact",
                 "significant": significant, "underpowered": False, "favors": "B"},
        per_criterion=[CriterionComparison(
            criterion_id="routing", mean_a=0.5, mean_b=0.9, delta=0.4,
            direction="B", p_value=0.01, ci_low=0.2, ci_high=0.6,
            significant=True, n=12)],
        flipped_cases=[
            FlippedCase(test_id="tc-3", a_passed=False, b_passed=True,
                        direction="gain"),
            FlippedCase(test_id="tc-7", a_passed=True, b_passed=False,
                        direction="loss")],
        mean_cost_a=0.01, mean_cost_b=0.02, total_cost_a=0.12, total_cost_b=0.24,
        p95_latency_a=120, p95_latency_b=200,
        winner=winner,
        verdict="B beats A on the suite (significant, McNemar p=0.016, n=12).")


class TestMarkdown:
    def test_presentable_no_placeholders(self):
        md = render_ab_markdown(_comparison())
        for section in ["A/B Comparison", "Verdict", "Variants",
                        "Overall success (paired)", "Per-criterion deltas",
                        "Flipped cases", "Cost & latency"]:
            assert section in md
        assert "McNemar" in md
        assert "significant" in md
        assert "routing" in md
        assert "tc-3" in md and "tc-7" in md          # flipped cases listed
        assert "fail → " in md.lower() or "fail ->" in md
        assert "tc-err" in md                          # excluded case surfaced
        assert not re.search(r"\{[a-z_]+\}|TODO|XXX|lorem", md)

    def test_no_difference_phrasing(self):
        c = _comparison(winner="tie", significant=False)
        c.verdict = "No significant difference between A and B (McNemar p=0.40, n=12)."
        md = render_ab_markdown(c)
        assert "No significant difference" in md
        assert "not significant" in md


class TestPdf:
    def test_renders_valid_pdf(self):
        pdf = render_ab_pdf(_comparison())
        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 1000
