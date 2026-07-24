"""Reporting (Step 10) — render a Scorecard into the client deliverable.

Sections: executive summary, per-case results, per-criterion breakdown with
judge rationales for failures, cost/latency stats, visibility tier and
calibration status, regression diff vs a previous scorecard, and a
recommendations section built from the worst-performing criteria.
"""

from __future__ import annotations

from agenttic.schema.rubric import Rubric
from agenttic.schema.scorecard import Scorecard


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
        f"# Agent Verification Report — `{sc.agent_id}`",
        "",
        f"Suite `{sc.suite_id}` v{sc.suite_version} · rubric `{sc.rubric_id}` "
        f"v{sc.rubric_version} · generated {sc.created_at:%Y-%m-%d %H:%M} UTC",
        "",
    ]
    lines += _verification_block(sc)
    lines += [
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

    return "\n".join(lines) + "\n"


def _verification_block(sc) -> list[str]:
    """The headline (SPEC-13 Step 64): what was never exercised, which properties
    held, and only then the pass rate — demoted to one line.

    A pass rate reported without a coverage model is an unscoped claim and is
    labelled as such (Hard Rule 56)."""
    cov = getattr(sc, "coverage", None) or {}
    asrt = cov.get("assertions") or {}
    out: list[str] = ["## Verification", ""]

    # --- 1. coverage: what was never exercised --------------------------------
    if cov.get("model_ref"):
        closure = cov.get("trace_closure", 0.0)
        target = cov.get("closure_target", 0.95)
        state = "CLOSED" if cov.get("closed") else "NOT CLOSED"
        out.append(f"**Coverage closure {closure:.0%}** of target {target:.0%} — "
                   f"{state}.")
        if cov.get("baseline"):
            out.append("")
            out.append(f"> {cov.get('limits', '')}")
        out.append("")
        out.append("| Coverpoint | Closure | Never exercised |")
        out.append("|---|---|---|")
        for cp_id, cp in (cov.get("per_coverpoint") or {}).items():
            unhit = ", ".join(f"`{u}`" for u in cp.get("unhit", [])) or "—"
            out.append(f"| {cp_id} | {cp.get('closure', 0):.0%} | {unhit} |")
        drift = cov.get("other_drift") or {}
        if drift:
            out.append("")
            out.append("Unmodelled situations landed in `other` for: "
                       + ", ".join(f"{k} ({v:.0%} of runs)" for k, v in drift.items())
                       + " — the coverage model is missing a dimension.")
    else:
        out.append("**No coverage model was applied to this run.** Nothing here "
                   "states what the suite never exercised.")
    out.append("")

    # --- 2. assertions --------------------------------------------------------
    if asrt:
        verdict = asrt.get("verdict", "PASS")
        out.append(f"**Assertions: {verdict}** — {asrt.get('violations', 0)} "
                   f"violation(s) of {asrt.get('total', 0)} properties; "
                   f"{asrt.get('unexercised', 0)} never exercised "
                   f"(unexercised is *not* evidence of correctness).")
        for v in (asrt.get("violated_properties") or [])[:6]:
            where = f" ({v['traces']})" if v.get("traces") else ""
            out.append(f"- ❌ `{v.get('assertion_id', '')}`{where} — "
                       f"{v.get('detail', '')}")
        unex = asrt.get("unexercised_properties") or []
        if unex:
            out.append("- Unexercised: " + ", ".join(f"`{u}`" for u in unex[:8]))
    else:
        out.append("**Assertions: not run** on this scorecard.")
    out.append("")

    # --- 3. the pass rate, demoted -------------------------------------------
    if sc.n_scored == 0:
        # Nothing was scored. Reporting 0% here would read as "the agent failed
        # everything" when in fact this is a scoring-configuration failure — the
        # same invariant the Executive summary protects.
        out.append("Pass rate (one line among several): **not available** — no "
                   "case could be scored (a scoring-configuration failure, not an "
                   "agent failure).")
    else:
        scoped = bool(cov.get("model_ref")) and not cov.get("baseline")
        label = (f"{_pct(sc.task_success_rate)}" if scoped
                 else f"{_pct(sc.task_success_rate)} — "
                      + ("scoped to a BASELINE coverage model only"
                         if cov.get("baseline")
                         else "**unscoped** (no coverage model)"))
        out.append(f"Pass rate (one line among several): {label}")
    out.append("")
    return out
