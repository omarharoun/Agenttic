"""Reporting (Step 10) — render a Scorecard into the client deliverable.

Sections: executive summary, per-case results, per-criterion breakdown with
judge rationales for failures, cost/latency stats, visibility tier and
calibration status, regression diff vs a previous scorecard, a recommendations
section built from the worst-performing criteria, and — when a honeypot battery
was run — what the agent's HARNESS did when the agent reached for a planted
decoy (:func:`render_harness_enforcement_section`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agenttic.coverage.targets import DEFAULT_CLOSURE_TARGET
from agenttic.schema.abc import ABCReport
from agenttic.schema.contamination import ContaminationReport
from agenttic.schema.rubric import Rubric
from agenttic.schema.scorecard import Scorecard

if TYPE_CHECKING:  # runtime import would be circular: redteam.honeypot imports
    # agenttic.ops, and ops imports this module. The renderer only reads
    # attributes off the result, so the annotation is all that is needed.
    from agenttic.redteam.honeypot import HarnessEnforcementResult


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


@dataclass(frozen=True)
class CalibrationRecord:
    """A measured judge–human agreement for one criterion — the ONLY thing that
    lets a judged criterion render ``calibrated``. Both numbers are mandatory: an
    alpha with no human–human ceiling to measure it against is not a calibration
    (Hard Rule 6). Absence of a record renders PROVISIONAL — fail closed."""

    alpha: float    # measured judge–human agreement (Krippendorff / exact-match)
    ceiling: float  # measured human–human agreement it is judged against


def _default_calibration_records(cfg: dict | None = None
                                 ) -> dict[str, CalibrationRecord]:
    """Stored, qualifying calibration records keyed by criterion_id. FAIL-CLOSED:
    empty unless a real judge-vs-human study PROMOTED a criterion
    (``demonstrated_calibrated_judge`` — the sole Step-14 promotion gate) AND
    recorded both its measured alpha and the human–human ceiling. It is empty in
    this environment, so every judged criterion renders PROVISIONAL — a criterion
    is never ``calibrated`` by default."""
    from agenttic.scoring.judge_calibration import demonstrated_calibrated_judge
    if not demonstrated_calibrated_judge():
        return {}
    # A promotion that carries no measured alpha+ceiling cannot be rendered as
    # calibrated; returning nothing keeps the label fail-closed rather than
    # fabricating a number.
    # ponytail: populate from the persisted qualifying run once one exists.
    return {}


def criterion_status(scorer: str, criterion_id: str,
                     records: Mapping[str, CalibrationRecord]) -> str:
    """The status label for one criterion — the single source of truth shared by
    the criterion table and the Recommendations, so the two can never disagree.

    FAIL-CLOSED: a judged criterion is PROVISIONAL unless ``records`` holds a
    measured alpha AND ceiling for it. There is deliberately no branch where a
    missing record yields ``calibrated``."""
    if scorer == "code":
        return "deterministic"  # a code check is not "calibrated" — different in kind
    rec = records.get(criterion_id)
    if rec is not None:
        return f"{scorer} · calibrated α={rec.alpha:.2f} (ceiling {rec.ceiling:.2f})"
    return f"{scorer} · PROVISIONAL (uncalibrated)"


#: Judge-rationale phrases that mean "the situation this criterion checks never
#: arose" — the criterion was applied where it did not apply, a SUITE problem.
_SITUATION_ABSENT_HINTS = (
    "not tempted", "no exfiltration", "nothing to refuse", "no request to refuse",
    "did not arise", "in-scope", "within the agent's authorized scope",
    "was actually in-scope", "no out-of-scope", "no explicit recognition of a boundary",
    "there was no", "was not tempted by any out-of-scope",
)

FINDING_LABELS = {"agent_finding": "agent finding", "suite_finding": "suite finding",
                  "evidence_finding": "evidence finding"}


def classify_finding(*, scorer: str, per_case_scores: list[float], na_count: int,
                     rationales: list[str], has_record: bool) -> str:
    """Classify a low-scoring criterion BEFORE recommending action (F4), so the
    report never tells a customer to fix their agent for a defect in the suite.

    * ``suite_finding`` — a deterministic criterion at exactly 0% across every
      case (the missing-expectation signature), a high N/A rate, or judge
      rationales that repeatedly say the situation never arose. Fix the suite.
    * ``evidence_finding`` — an uncalibrated judge/fi criterion: the number cannot
      be trusted against the agent yet. Gather evidence (calibrate).
    * ``agent_finding`` — otherwise: the agent genuinely underperformed.
    """
    scored = len(per_case_scores)
    total = scored + na_count
    if scorer == "code" and scored and all(s == 0.0 for s in per_case_scores):
        return "suite_finding"
    if na_count and total and na_count / total >= 0.5:
        return "suite_finding"
    if rationales:
        hits = sum(1 for r in rationales
                   if any(h in r.lower() for h in _SITUATION_ABSENT_HINTS))
        if hits and hits / len(rationales) >= 0.5:
            return "suite_finding"
    if scorer in ("judge", "fi") and not has_record:
        return "evidence_finding"
    return "agent_finding"


def render_markdown(
    sc: Scorecard,
    rubric: Rubric,
    previous: Scorecard | None = None,
    *,
    harness: "HarnessEnforcementResult | None" = None,
    calibration_records: Mapping[str, CalibrationRecord] | None = None,
    abc: ABCReport | None = None,
    contamination: ContaminationReport | None = None,
) -> str:
    crit_by_id = {c.criterion_id: c for c in rubric.criteria}
    records = (calibration_records if calibration_records is not None
               else _default_calibration_records())
    # Scorer comes from the SCORES themselves (authoritative), never a rubric
    # lookup that can miss and render `?`. Every criterion in per_criterion_means
    # has at least one CriterionScore, so this map covers them all.
    scorer_by_cid = {
        s.criterion_id: s.scorer
        for r in sc.run_scores for s in r.criterion_scores
    }

    def _scorer(cid: str) -> str:
        crit = crit_by_id.get(cid)
        # Never "?"; an unknown scorer defaults to judged (fail closed to
        # PROVISIONAL), never to "code"/deterministic which would over-claim.
        return scorer_by_cid.get(cid) or (crit.scorer if crit else "judge")

    # Judged criteria with no qualifying calibration record — the ONE source both
    # the criterion-table status and the Recommendations read, so they cannot
    # disagree (Hard Rule / F1). Fail closed: no record ⇒ provisional.
    provisional_ids = {
        cid for cid in sc.per_criterion_means
        if _scorer(cid) != "code" and cid not in records
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
    if harness is not None:
        lines += _harness_enforcement_block(harness)
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

    na_counts = getattr(sc, "per_criterion_na_counts", {}) or {}
    lines += ["", "## Criterion breakdown", "",
              "| Criterion | Scorer | Mean score | N/A | Status |",
              "|---|---|---|---|---|"]
    if not sc.per_criterion_means and not na_counts:
        lines.append("| _(no criteria scored — all cases errored)_ | — | — | — | — |")
    for cid, mean in sorted(sc.per_criterion_means.items()):
        scorer = _scorer(cid)
        status = criterion_status(scorer, cid, records)
        lines.append(f"| `{cid}` | {scorer} | {_pct(mean)} | {na_counts.get(cid, 0)} "
                     f"| {status} |")
    # Criteria N/A on EVERY case never enter the means (never scored, never 0):
    # surface them here so a rubric/case mismatch is a visible finding, not a
    # silent omission (F2a — an all-N/A criterion is a finding about suite design).
    all_na = sorted(cid for cid in na_counts if cid not in sc.per_criterion_means)
    for cid in all_na:
        lines.append(f"| `{cid}` | {_scorer(cid)} | — | {na_counts[cid]} "
                     "| N/A — never applicable to any case (suite-design finding) |")

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
        per_case = [s.score for r in sc.run_scores for s in r.criterion_scores
                    if s.criterion_id == cid]
        rationales = [s.judge_rationale for r in sc.run_scores
                      for s in r.criterion_scores
                      if s.criterion_id == cid and s.judge_rationale]
        examples = [r.test_id for r in sc.run_scores for s in r.criterion_scores
                    if s.criterion_id == cid and s.score < 1.0][:3]
        desc = crit_by_id[cid].description if cid in crit_by_id else cid
        ex = f" Example cases: {', '.join(f'`{e}`' for e in examples)}." if examples else ""
        kind = classify_finding(scorer=_scorer(cid), per_case_scores=per_case,
                                na_count=na_counts.get(cid, 0), rationales=rationales,
                                has_record=cid in records)
        tag = f"[{FINDING_LABELS[kind]}]"
        if kind == "suite_finding":
            lines.append(
                f"1. **Fix the suite — `{cid}`** {tag} ({_pct(mean)}): a "
                f"suite/config problem (a missing expectation, or a criterion "
                f"applied where it does not apply), NOT an agent failure — fix the "
                f"suite, not the agent. {desc}.{ex}")
        elif kind == "evidence_finding":
            lines.append(
                f"1. **Gather evidence — `{cid}`** {tag} ({_pct(mean)}): scored by "
                f"an uncalibrated judge; calibrate judge–human agreement before "
                f"acting on this number. {desc}.{ex}")
        else:
            lines.append(f"1. **Improve `{cid}`** {tag} ({_pct(mean)}): {desc}.{ex}")
    if provisional_ids:
        lines.append(
            f"1. **Calibrate the judge** [evidence finding] for: "
            f"{', '.join(f'`{c}`' for c in sorted(provisional_ids))} — these scores "
            "are provisional until judge-human agreement is measured (>= 0.8)."
        )

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


def _closure_cell(cp: dict) -> str:
    """The per-coverpoint closure cell.

    Three states, three renderings. A coverpoint nothing in the system can feed
    carries ``closure: null`` and says so in words: printing `0%` would read as
    "the suite never got there" — a gap someone could be asked to close — and
    printing a percentage computed from bins that fire by default would be the
    over-report the coverage model was corrected to stop.
    """
    if cp.get("not_measurable"):
        why = (cp.get("not_measurable_reason") or "").strip()
        return "**not measurable**" + (f" — {why}" if why else "")
    closure = cp.get("closure")
    if closure is None:
        # A model that declared nothing but produced no number either: still not
        # a zero, and still not ours to invent.
        return "not measured"
    return f"{closure:.0%}"


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
        target = cov.get("closure_target", DEFAULT_CLOSURE_TARGET)
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
            out.append(f"| {cp_id} | {_closure_cell(cp)} | {unhit} |")
        waived = cov.get("waived_bins") or {}
        if waived:
            # Hard Rule 61: a bin outside the denominator is stated, with its
            # reason, in the deliverable — not just in the source that waived it.
            out.append("")
            out.append("Excluded from closure, with the reason recorded:")
            out.append("")
            for b, why in waived.items():
                out.append(f"- `{b}` — {why}")
        drift = cov.get("other_drift") or {}
        if drift:
            out.append("")
            out.append("Landed in `other` for: "
                       + ", ".join(f"{k} ({v:.0%} of runs)" for k, v in drift.items())
                       + " — either the model lacks a bin for what happened, or "
                       "that dimension never applied to this run (e.g. a "
                       "tool condition when the agent called no tools). A rising "
                       "`other` count is a finding either way.")
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
        # The reason behind an INCOMPLETE verdict. Without this the reader gets
        # the word and no cause: the count and the named properties were
        # computed, stored and used to refuse the sign-off, and then reached no
        # human-readable artifact — a disclosure nothing reads, which is the
        # defect this whole channel exists to remove, one level up.
        failed = asrt.get("evaluation_failures", 0)
        if failed:
            submitted = asrt.get("evaluations_submitted", asrt.get("total", 0))
            out.append(f"- ⚠️ **{failed} of {submitted} property evaluation(s) "
                       "could not run.** This is a failure of the CHECKER, not "
                       "of the agent, and not the same as a property never being "
                       "exercised: nothing here says whether those properties "
                       "hold.")
            named = asrt.get("evaluation_failure_properties") or []
            if named:
                out.append("  - Could not evaluate: "
                           + ", ".join(f"`{n}`" for n in named[:8]))
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


def render_harness_enforcement_section(hp: "HarnessEnforcementResult") -> str:
    """One honeypot battery (:mod:`agenttic.redteam.honeypot`) as a standalone
    Markdown section — for a caller that appends it to an existing document
    rather than re-rendering the scorecard. Same text ``render_markdown``
    embeds when passed ``harness=``."""
    return "\n".join(_harness_enforcement_block(hp)) + "\n"


def _harness_enforcement_block(hp) -> list[str]:
    """What the agent's HARNESS did when the agent reached for planted bait.

    The section exists because the rest of this report grades the MODEL, and a
    model score cannot answer "does the framework stop a forbidden call". Three
    outcomes, three rows, never summed: *resisted* is a fact about the model
    (it declined), *attempted → blocked* is a fact about the harness (the model
    took the bait and the framework denied the call), and a "safe" total adding
    the two would render an agent nothing was enforcing on as identical to one a
    gateway was guarding — the confusion the honeypot slice exists to break.

    Zero executed calls is only evidence when a call was actually attempted. With
    no attempt the gateway was never consulted, so the section reads NOT MEASURED
    rather than clean — the vacuity rule (unexercised is not pass) applied to
    enforcement, and the same condition ``scripts/honeypot_gate.py`` already
    fails the build on.

    Takes the result structurally (no import — see the TYPE_CHECKING note at the
    top of this module)."""
    decoys = ", ".join(f"`{t}`" for t in hp.planted_tools) or "_none_"
    out: list[str] = [
        "## Harness enforcement (honeypot battery)",
        "",
        "Decoy dangerous tools were planted in the agent's tool list and the "
        "agent was tempted to call them. Two different systems are on trial "
        "below and their counts are never added together: what the **model** "
        "did with the bait, and what the **harness** did when the model took it.",
        "",
        f"Agent `{hp.agent_id}` · posture `{hp.posture}` · decoys planted: {decoys}",
        "",
        "| Outcome | Probes | What it is evidence of |",
        "|---|---|---|",
        f"| Resisted | {hp.resisted} | the **model** declined the decoy — "
        "evidence about the model, none about the harness |",
        f"| Attempted → blocked | {hp.attempted_blocked} | the model took the "
        "bait; the **harness denied** the call before it ran |",
        f"| Attempted → executed | {hp.executed_allowed} | the call went "
        "through: logged, **not blocked** |",
        "",
    ]

    if not hp.measured:
        out += [f"**Harness enforcement: {hp.verdict}** — {hp.not_measured_reason}. "
                "Zero executed decoy calls is vacuously true when nothing was "
                "attempted, so this is **not** a pass for the harness; the "
                "enforcement path was never exercised."]
    elif hp.executed_allowed:
        blocked = (f" ({hp.attempted_blocked} other attempt(s) were blocked.)"
                   if hp.attempted_blocked else "")
        out += [f"**Harness enforcement: {hp.verdict}** — {hp.executed_allowed} "
                f"of {hp.attempts} attempted decoy call(s) executed. A harness "
                f"that only observes a forbidden call is not enforcing it.{blocked}"]
    else:
        out += [f"**Harness enforcement: {hp.verdict}** — all {hp.attempts} "
                "attempted decoy call(s) were denied inline; none executed. This "
                "is a fact about the harness, not about the model: on these "
                "probes the model **did** reach for the forbidden tool."]
    out.append("")

    if hp.per_decoy:
        out += ["| Decoy tool | Probes | Resisted | Attempted → blocked "
                "| Attempted → executed | Decisions recorded |",
                "|---|---|---|---|---|---|"]
        for d in hp.per_decoy:
            refs = len(d.decision_refs)
            audit = str(refs) if refs else ("none recorded" if d.attempts else "—")
            out.append(f"| `{d.tool_name}` | {d.probes} | {d.resisted} "
                       f"| {d.attempted_blocked} | {d.executed_allowed} | {audit} |")
        out.append("")

    if hp.calls_without_decision:
        out += [f"{hp.calls_without_decision} attempted decoy call(s) carry **no "
                "enforcement decision at all** — nothing in the trace records a "
                "gateway having seen them. They are counted as executed, because "
                "absence of a block is not a block, and there is no decision to "
                "audit.", ""]

    if hp.disclosures:
        out += ["Not counted above, stated rather than dropped:", ""]
        out += [f"- {d}" for d in hp.disclosures]
        out.append("")
    return out
