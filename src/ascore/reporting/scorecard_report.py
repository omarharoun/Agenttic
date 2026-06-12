"""Reporting (Step 10) — render a Scorecard into the client deliverable.

Sections: executive summary, per-case results, per-criterion breakdown with
judge rationales for failures, cost/latency stats, visibility tier and
calibration status, regression diff vs a previous scorecard, and a
recommendations section built from the worst-performing criteria.
"""

from __future__ import annotations

from ascore.schema.rubric import Rubric
from ascore.schema.scorecard import Scorecard


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def render_markdown(
    sc: Scorecard,
    rubric: Rubric,
    previous: Scorecard | None = None,
) -> str:
    crit_by_id = {c.criterion_id: c for c in rubric.criteria}
    calibrated_ids = {
        s.criterion_id for r in sc.run_scores for s in r.criterion_scores
        if s.calibrated
    }
    provisional_ids = {
        s.criterion_id for r in sc.run_scores for s in r.criterion_scores
        if not s.calibrated
    }
    n = len(sc.run_scores)
    n_pass = sum(1 for r in sc.run_scores if r.passed)
    tier_note = (
        "Full trajectory-level scoring (glass-box instrumentation)."
        if sc.visibility_tier == "glass_box"
        else "**Black-box tier**: input/output scoring only; trajectory criteria "
             "were not assessable. Instrumenting the agent unlocks deeper diagnostics."
    )

    lines = [
        f"# Agent Evaluation Scorecard — `{sc.agent_id}`",
        "",
        f"Suite `{sc.suite_id}` v{sc.suite_version} · rubric `{sc.rubric_id}` "
        f"v{sc.rubric_version} · generated {sc.created_at:%Y-%m-%d %H:%M} UTC",
        "",
        "## Executive summary",
        "",
        f"The agent passed {n_pass} of {n} test cases "
        f"(task success rate {_pct(sc.task_success_rate)}), at a mean cost of "
        f"${sc.mean_cost_usd:.4f} per run and p95 latency of "
        f"{sc.p95_latency_ms:.0f} ms. {tier_note}",
        "",
        "## Results by test case",
        "",
        "| Test case | Result | Cost (USD) | Latency (ms) | Steps |",
        "|---|---|---|---|---|",
    ]
    for r in sc.run_scores:
        lines.append(
            f"| `{r.test_id}` | {'PASS' if r.passed else 'FAIL'} "
            f"| {r.cost_usd:.4f} | {r.latency_ms:.0f} | {r.steps} |"
        )

    lines += ["", "## Criterion breakdown", "",
              "| Criterion | Scorer | Mean score | Status |", "|---|---|---|---|"]
    for cid, mean in sorted(sc.per_criterion_means.items()):
        crit = crit_by_id.get(cid)
        scorer = crit.scorer if crit else "?"
        status = "calibrated" if cid in calibrated_ids and cid not in provisional_ids \
            else "PROVISIONAL (uncalibrated judge)"
        if scorer == "code":
            status = "deterministic"
        lines.append(f"| `{cid}` | {scorer} | {_pct(mean)} | {status} |")

    failures = [
        (r.test_id, s)
        for r in sc.run_scores for s in r.criterion_scores
        if s.score < 1.0 and s.judge_rationale
    ]
    if failures:
        lines += ["", "### Judge rationales for sub-perfect scores", ""]
        for test_id, s in failures[:15]:
            lines.append(f"- `{test_id}` / `{s.criterion_id}` "
                         f"(score {s.score}): {s.judge_rationale}")

    if previous is not None:
        lines += ["", "## Regression vs previous run", "",
                  f"Compared to scorecard `{previous.scorecard_id}` "
                  f"({previous.created_at:%Y-%m-%d}):", ""]
        delta = sc.task_success_rate - previous.task_success_rate
        arrow = "improved" if delta > 0 else ("regressed" if delta < 0 else "unchanged")
        lines.append(f"- Task success rate {arrow}: "
                     f"{_pct(previous.task_success_rate)} → "
                     f"{_pct(sc.task_success_rate)}")
        for cid, mean in sorted(sc.per_criterion_means.items()):
            prev = previous.per_criterion_means.get(cid)
            if prev is not None and abs(mean - prev) > 1e-9:
                lines.append(f"- `{cid}`: {_pct(prev)} → {_pct(mean)}")

    worst = sorted(sc.per_criterion_means.items(), key=lambda kv: kv[1])[:3]
    lines += ["", "## Recommendations", ""]
    for cid, mean in worst:
        examples = [r.test_id for r in sc.run_scores
                    for s in r.criterion_scores
                    if s.criterion_id == cid and s.score < 1.0][:3]
        desc = crit_by_id[cid].description if cid in crit_by_id else cid
        ex = f" Example cases: {', '.join(f'`{e}`' for e in examples)}." if examples else ""
        lines.append(f"1. **Improve `{cid}`** ({_pct(mean)}): {desc}.{ex}")
    if provisional_ids:
        lines.append(
            f"1. **Calibrate the judge** for: "
            f"{', '.join(f'`{c}`' for c in sorted(provisional_ids))} — these scores "
            "are provisional until judge-human agreement is measured (>= 0.8)."
        )

    return "\n".join(lines) + "\n"
