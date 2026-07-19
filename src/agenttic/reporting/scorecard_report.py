"""Reporting (Step 10) — render a Scorecard into the client deliverable.

Sections: executive summary, per-case results, per-criterion breakdown with
judge rationales for failures, cost/latency stats, visibility tier and
calibration status, regression diff vs a previous scorecard, and a
recommendations section built from the worst-performing criteria.
"""

from __future__ import annotations

from agenttic.schema.abc import ABCReport
from agenttic.schema.contamination import ContaminationReport
from agenttic.schema.rubric import Rubric
from agenttic.schema.scorecard import Scorecard


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def render_markdown(
    sc: Scorecard,
    rubric: Rubric,
    previous: Scorecard | None = None,
    abc: ABCReport | None = None,
    contamination: ContaminationReport | None = None,
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
    errored = [r for r in sc.run_scores if r.scoring_error]
    scored = [r for r in sc.run_scores if not r.scoring_error]
    n_err = len(errored)
    n_scored = len(scored)
    n_pass = sum(1 for r in scored if r.passed)
    tier_note = (
        "Full trajectory-level scoring (glass-box instrumentation)."
        if sc.visibility_tier == "glass_box"
        else "**Black-box tier**: input/output scoring only; trajectory criteria "
             "were not assessable. Instrumenting the agent unlocks deeper diagnostics."
    )

    cost_note = (f"Mean cost ${sc.mean_cost_usd:.4f} per run, p95 latency "
                 f"{sc.p95_latency_ms:.0f} ms. {tier_note}")
    if n_scored == 0:
        # Nothing scored — do NOT report this as 0% / all-failed; it's a scoring
        # configuration failure, not the agent failing the task.
        summary = (
            f"⚠ **No test cases could be scored.** All {n} case(s) errored during "
            f"scoring (the agent ran, but the scoring config was invalid — see "
            f"**Errored cases** below). Task success rate is not available. "
            f"{cost_note}")
    else:
        err_note = (f" {n_err} case(s) errored during scoring and were excluded "
                    f"from the rate (see **Errored cases**)." if n_err else "")
        summary = (
            f"The agent passed {n_pass} of {n_scored} scored case(s) "
            f"(task success rate {_pct(sc.task_success_rate)}).{err_note} {cost_note}")

    lines = [
        f"# Agent Evaluation Scorecard — `{sc.agent_id}`",
        "",
        f"Suite `{sc.suite_id}` v{sc.suite_version} · rubric `{sc.rubric_id}` "
        f"v{sc.rubric_version} · generated {sc.created_at:%Y-%m-%d %H:%M} UTC",
        "",
        "## Executive summary",
        "",
        summary,
        "",
        "## Cost",
        "",
        f"- Agent execution: **${sc.total_cost_usd:.4f}** "
        f"(${sc.mean_cost_usd:.4f}/run × {n} runs)",
        f"- Scoring (judge): **${sc.total_scoring_cost_usd:.4f}**",
        f"- Total run cost: **${sc.total_cost_usd + sc.total_scoring_cost_usd:.4f}**",
        "",
        "## Results by test case",
        "",
        "| Test case | Result | Cost (USD) | Latency (ms) | Steps |",
        "|---|---|---|---|---|",
    ]
    for r in sc.run_scores:
        result = "ERROR" if r.scoring_error else ("PASS" if r.passed else "FAIL")
        lines.append(
            f"| `{r.test_id}` | {result} "
            f"| {r.cost_usd:.4f} | {r.latency_ms:.0f} | {r.steps} |"
        )

    if errored:
        lines += ["", "## Errored cases", "",
                  f"{n_err} case(s) could not be scored. These are scoring/config "
                  "failures, **not** agent task failures, and are excluded from the "
                  "success rate:", "",
                  "| Test case | Error |", "|---|---|"]
        for r in errored:
            lines.append(f"| `{r.test_id}` | {(r.scoring_error or '').replace('|', '\\|')[:160]} |")

    lines += ["", "## Criterion breakdown", "",
              "| Criterion | Scorer | Mean score | Status |", "|---|---|---|---|"]
    if not sc.per_criterion_means:
        lines.append("| _(no criteria scored — all cases errored)_ | — | — | — |")
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

    # SPEC-7 32 — policy compliance (governance buyers read this first). Groups
    # the policy-tagged criteria; cross-references the escalation/autonomy policy.
    policy_crits = [c for c in rubric.criteria if "policy" in getattr(c, "tags", [])]
    if policy_crits:
        lines += ["", "## Policy compliance", "",
                  "Criteria derived from the suite's policy document, cross-referenced "
                  "with the agent's escalation/autonomy policy (SPEC-2).", "",
                  "| Criterion | Mean | Type |", "|---|---|---|"]
        for c in policy_crits:
            mean = sc.per_criterion_means.get(c.criterion_id)
            m = f"{mean * 100:.0f}%" if mean is not None else "—"
            lines.append(f"| `{c.criterion_id}` | {m} | {c.scorer} |")

    # SPEC-7 30 — simulated-user label (Hard Rule 31): a proxy, not a human.
    if getattr(sc, "user_source", "none") == "simulated":
        lines += ["", "> **Evaluated against simulated users.** These results were "
                  "produced with an LLM user simulator — a proxy, not a human. Treat "
                  "them as provisional; human-user results are the calibration ceiling."]

    # SPEC-7 31 — reliability as consistency (pass^k) with the flakiness gap named.
    if sc.pass_k_curve:
        curve = {int(k): v for k, v in sc.pass_k_curve.items()}
        kmax = max(curve)
        lines += ["", "## Reliability (pass^k)", "",
                  f"Trials per case: **{sc.trials_per_case}**.", "",
                  "| k | pass^k |", "|---|---|"]
        for kp in sorted(curve):
            lines.append(f"| {kp} | {curve[kp] * 100:.0f}% |")
        if 1 in curve:
            gap = curve[1] - curve[kmax]
            lines += ["", f"Succeeds **{curve[1] * 100:.0f}%** of the time once; "
                      f"**{curve[kmax] * 100:.0f}%** of the time consistently across "
                      f"{kmax} attempts. The **flakiness gap** is "
                      f"{gap * 100:.0f} points."]
    else:
        lines += ["", "## Reliability (pass^k)", "",
                  "single-trial (k=1) — reliability across repeated attempts was not "
                  "measured. Certification-grade claims recommend k >= 4."]

    # SPEC-6 26.1 — benchmark-rigor scorecard (the bureau certifying its own
    # instrument). Items with no evidence read N/A, never estimated upward.
    if abc is not None:
        overall = abc.overall
        headline = f"{overall:.2f}" if overall is not None else "N/A"
        lines += ["", "## Benchmark rigor (ABC)", "",
                  f"Benchmark rigor: **{headline}** (ABC) — how well this suite meets "
                  "the Agentic Benchmark Checklist, from evidence we hold. Items we "
                  "cannot evidence are shown N/A, never estimated upward.", "",
                  "| Item | Aspect | Score | Evidence |", "|---|---|---|---|"]
        for it in abc.items:
            s = f"{it.score:.2f}" if it.score is not None else "N/A"
            lines.append(f"| {it.item_id} | {it.name} | {s} | {it.evidence} |")

    # SPEC-6 28 — the standard contamination line: origin, canary, exposure.
    if contamination is not None:
        lines += ["", "## Contamination", "", contamination.report_line()]

    return "\n".join(lines) + "\n"
