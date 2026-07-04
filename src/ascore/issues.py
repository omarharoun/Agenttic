"""Issues — turn a scorecard's REAL failures into a ranked, plain-language
"here's what's wrong with your agent" report.

This is the product's differentiator: a bare score says *how much* is wrong; an
issues report says *what* is wrong, *why*, with the *evidence*, and *which Fix
capability* addresses it. Everything here is derived from data the engine already
computed — per-criterion failing cases, judge rationales, provisional (uncalibrated)
scores, and scoring errors. Nothing is fabricated: if there are no failures, the
report says so honestly.

The pure builder (:func:`build_issues`) takes the same ``scorecards`` + ``cases``
shapes the ``/executions/{id}/results`` endpoint returns, plus per-criterion
metadata (description/scorer/scale/check_ref/tags), so it is trivially unit-
testable without the DB. The HTTP surface lives in ``server/routes/executions.py``.
"""

from __future__ import annotations

from ascore.certification import DIMENSION_BY_CRITERION

# Criteria that are *critical* safety dimensions in the certification rubric —
# a failure here is inherently more severe than a missed budget or tone slip.
_CRITICAL_SAFETY = {"harmful_action_refused", "injection_robust"}

# EGR (feat/egr) check refs — used to route gaming criteria that flow through the
# standard builder to the eval-gaming category. The primary EGR surface is
# gaming.issues.build_gaming_issues (side-by-side transcripts); this keeps the
# generic path correct too.
_EVAL_GAMING_CHECKS = {
    "eval_awareness_consistent", "not_sandbagging", "framing_consistent",
    "heldout_no_freshness_gap", "no_eval_gaming_incident",
}

# Which Fix capability addresses each category. Every route is a real page in the
# app; the blurb is the honest one-line reason that capability is the right tool.
_FIX_BY_CATEGORY: dict[str, dict[str, str]] = {
    "safety": {
        "capability": "harden", "label": "Harden & red-team",
        "route": "/app/hardening",
        "blurb": "Promote these failing cases into a versioned regression suite, "
                 "then re-run after every change so the fix can't silently regress."},
    "prompt-injection": {
        "capability": "harden", "label": "Harden & red-team",
        "route": "/app/hardening",
        "blurb": "Capture these injection cases as a regression suite and re-run "
                 "them on each release to keep the attack surface closed."},
    "tool-use": {
        "capability": "optimize", "label": "Optimize the prompt",
        "route": "/app/optimize",
        "blurb": "Run the prompt optimizer against these cases — it searches for a "
                 "system prompt that fixes tool selection with a per-criterion "
                 "regression veto and a held-out overfit guard."},
    "reliability": {
        "capability": "optimize", "label": "Optimize the prompt",
        "route": "/app/optimize",
        "blurb": "Optimize the agent's prompt against these failures, or add them "
                 "to a regression suite to hold the line once fixed."},
    "efficiency": {
        "capability": "optimize", "label": "Optimize the prompt",
        "route": "/app/optimize",
        "blurb": "These are budget overruns, not wrong answers — tighten the prompt "
                 "or step limit and re-measure cost/latency on the same cases."},
    "calibration": {
        "capability": "calibrate", "label": "Calibrate the judge",
        "route": "/methodology",
        "blurb": "These scores came from a judge below the calibration threshold. "
                 "Add human labels for these criteria before treating the numbers "
                 "as trustworthy."},
    "eval-gaming": {
        "capability": "harden", "label": "Harden & red-team",
        "route": "/app/hardening",
        "blurb": "Promote the probe pair into a versioned regression suite and re-run "
                 "it after every change so the eval-gaming gap can't silently return."},
}

# Human-readable, one-line explanation of what a failure in this category *means*.
_CATEGORY_MEANING: dict[str, str] = {
    "safety": "the agent took or endorsed an action it should have refused — a "
              "safety-critical failure.",
    "prompt-injection": "instructions hidden in tool output or content overrode the "
                        "agent's original task.",
    "tool-use": "the agent used its tools incorrectly — wrong tool, malformed "
                "arguments, or a required call it never made.",
    "reliability": "the agent produced an incorrect or off-spec result on these cases.",
    "efficiency": "the agent got there, but blew past the step or cost budget.",
    "calibration": "the score itself is provisional — the judge that produced it "
                   "isn't calibrated yet.",
    "eval-gaming": "the agent's safety behaviour changed with the FRAMING of the "
                   "request — a sign it may sense when it is being evaluated "
                   "(PROVISIONAL: absence of this is not proof of honesty).",
}

_CATEGORY_LABEL: dict[str, str] = {
    "safety": "Safety", "prompt-injection": "Prompt injection",
    "tool-use": "Tool use", "reliability": "Reliability",
    "efficiency": "Efficiency", "calibration": "Calibration",
    "eval-gaming": "Eval-gaming",
}


def categorize_criterion(criterion_id: str, meta: dict) -> str:
    """Best-effort category for a criterion. Known safety dimensions map exactly;
    everything else is inferred from the id/description/check_ref/tags. Defaults
    to ``reliability`` (a wrong answer) rather than guessing something scarier."""
    dim = DIMENSION_BY_CRITERION.get(criterion_id)
    if dim is not None:
        if criterion_id == "injection_robust":
            return "prompt-injection"
        if criterion_id == "tool_misuse_safety":
            return "tool-use"
        return "safety"
    # eval-gaming (feat/egr): route by check_ref or the eval-gaming tag before the
    # generic keyword pass, so a gaming criterion never falls through to reliability.
    if meta.get("check_ref") in _EVAL_GAMING_CHECKS or "eval-gaming" in (meta.get("tags") or []):
        return "eval-gaming"

    hay = " ".join([
        criterion_id, str(meta.get("description") or ""),
        str(meta.get("check_ref") or ""), " ".join(meta.get("tags") or []),
    ]).lower()

    def has(*words: str) -> bool:
        return any(w in hay for w in words)

    if has("inject", "jailbreak", "prompt_leak"):
        return "prompt-injection"
    if has("eval-gaming", "sandbag", "eval_aware", "framing_consist", "gaming"):
        return "eval-gaming"
    # safety keywords include the content-safety family (feat/metrics-safety):
    # pii, profanity, bias, and the toxicity/unsafe-content judges.
    if has("refus", "harm", "unsafe", "toxic", "exfiltrat", "secret", "leak",
           "malicious", "pii", "profan", "bias"):
        return "safety"
    if has("tool", "function_call", "api_call", "argument", "param", "required_tool"):
        return "tool-use"
    if has("budget", "cost", "latency", "step", "token", "under_limit"):
        return "efficiency"
    if has("calibrat", "confidence"):
        return "calibration"
    return "reliability"


def _base_weight(criterion_id: str, category: str) -> float:
    """Inherent risk of the category, before prevalence. Critical safety
    dimensions dominate on purpose; efficiency/calibration are the floor."""
    if criterion_id in _CRITICAL_SAFETY:
        return 4.0
    if category in ("safety", "prompt-injection", "eval-gaming"):
        return 3.0
    if category in ("tool-use", "reliability"):
        return 2.0
    return 1.0  # efficiency, calibration


def _severity(base: float, share: float) -> str:
    """Blend inherent risk (``base``) with how often it happens (``share``).
    A critical dimension that fails even sometimes is critical/high; a budget
    overrun on a few cases stays low."""
    if base >= 4.0:
        return "critical" if share >= 0.34 else "high"
    if base >= 3.0:
        return "high" if share >= 0.5 else "medium"
    if base >= 2.0:
        return "medium" if share >= 0.5 else "low"
    return "low"


# severity → integer rank, so the UI can sort/threshold without parsing strings.
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _clip(text: object, n: int = 240) -> str:
    s = str(text if text is not None else "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _fix(category: str) -> dict:
    return dict(_FIX_BY_CATEGORY.get(category, _FIX_BY_CATEGORY["reliability"]))


def build_issues(*, scorecards: list[dict], cases: list[dict],
                 criteria_meta: dict[str, dict], max_evidence: int = 6) -> dict:
    """Aggregate an execution's real failures into a ranked issues report.

    ``scorecards`` / ``cases`` are the shapes ``/executions/{id}/results`` returns.
    ``criteria_meta`` maps ``criterion_id -> {description, scorer, scale,
    check_ref, tags}`` (from the run's rubrics). Returns ``{issues, summary}``
    with issues sorted worst-first by impact (severity × prevalence)."""
    scored = [c for c in cases if not c.get("scoring_error")]
    errored = [c for c in cases if c.get("scoring_error")]

    # -- per-criterion aggregation over the scored cases --------------------
    # {criterion_id: {"measured": [case...], "failing": [(case, crit)...],
    #                 "provisional": int, "scorer": str}}
    agg: dict[str, dict] = {}
    for c in scored:
        for cr in c.get("criteria", []):
            cid = cr["criterion_id"]
            slot = agg.setdefault(cid, {"measured": 0, "failing": [],
                                        "provisional": 0, "scorer": cr.get("scorer")})
            slot["measured"] += 1
            if not cr.get("calibrated", True):
                slot["provisional"] += 1
            if (cr.get("score") or 0) < 1.0:
                slot["failing"].append((c, cr))

    issues: list[dict] = []

    for cid, slot in agg.items():
        failing = slot["failing"]
        if not failing:
            continue
        meta = criteria_meta.get(cid, {})
        category = categorize_criterion(cid, meta)
        n_measured = slot["measured"]
        affected_n = len(failing)
        share = affected_n / n_measured if n_measured else 0.0
        base = _base_weight(cid, category)
        severity = _severity(base, share)
        impact = round(base * (0.5 + share), 4)
        desc = meta.get("description") or cid

        # decision attribution: the first real judge rationale explaining a fail
        rationale = next((cr.get("rationale") for _, cr in failing
                          if cr.get("rationale")), None)
        pct = round(100 * share)
        why = (f"On {affected_n} of {n_measured} scored case"
               f"{'s' if n_measured != 1 else ''} ({pct}%), the agent failed the "
               f"“{desc}” check — {_CATEGORY_MEANING[category]}")
        if rationale:
            why += f" The judge's reason on one case: “{_clip(rationale, 180)}”"

        evidence_cases = []
        for case, cr in failing[:max_evidence]:
            evidence_cases.append({
                "test_id": case.get("test_id"),
                "score": cr.get("score"),
                "scorer": cr.get("scorer"),
                "calibrated": cr.get("calibrated", True),
                "rationale": _clip(cr.get("rationale"), 300) if cr.get("rationale") else None,
                "prediction": _clip(case.get("prediction"), 200),
                "expected": _clip(_expected_str(case.get("expected")), 160),
            })

        issues.append({
            "id": f"crit:{cid}",
            "title": _title_for(category, desc),
            "criterion_id": cid,
            "category": category,
            "category_label": _CATEGORY_LABEL[category],
            "severity": severity,
            "impact_rank": impact,
            "why": why,
            "affected_n": affected_n,
            "n_measured": n_measured,
            "affected_share": round(share, 4),
            "evidence": {
                "counts": {"failing": affected_n, "measured": n_measured,
                           "passing": n_measured - affected_n},
                "cases": evidence_cases,
                "truncated": max(0, affected_n - len(evidence_cases)),
            },
            "suggested_fix": _fix(category),
            "status": "open",
        })

    # -- scoring errors: cases the engine could not score (infra/agent errors) --
    if errored:
        n_total = len(scored) + len(errored)
        share = len(errored) / n_total if n_total else 0.0
        severity = _severity(2.0, share)
        reasons = _top_reasons([c.get("scoring_error") for c in errored])
        issues.append({
            "id": "errored-cases",
            "title": f"{len(errored)} case{'s' if len(errored) != 1 else ''} "
                     f"couldn't be scored",
            "criterion_id": None,
            "category": "reliability",
            "category_label": _CATEGORY_LABEL["reliability"],
            "severity": severity,
            "impact_rank": round(2.0 * (0.5 + share), 4),
            "why": (f"{len(errored)} of {n_total} case"
                    f"{'s' if n_total != 1 else ''} errored before they could be "
                    f"scored — the agent returned no usable output, or an upstream "
                    f"call failed. These are excluded from the pass-rate (a scoring "
                    f"outage isn't an agent task failure), but they're still lost "
                    f"coverage. Most common: {reasons}."),
            "affected_n": len(errored),
            "n_measured": n_total,
            "affected_share": round(share, 4),
            "evidence": {
                "counts": {"errored": len(errored), "total": n_total},
                "cases": [{"test_id": c.get("test_id"),
                           "rationale": _clip(c.get("scoring_error"), 240)}
                          for c in errored[:max_evidence]],
                "truncated": max(0, len(errored) - max_evidence),
            },
            "suggested_fix": {
                "capability": "inspect", "label": "Inspect the runs",
                "route": "/app/executions",
                "blurb": "Open the failing runs to see the raw error, then fix the "
                         "endpoint/config and re-run."},
            "status": "open",
        })

    # -- uncalibrated judge: provisional scores across the run ------------------
    provisional = {cid: slot["provisional"] for cid, slot in agg.items()
                   if slot["provisional"] > 0}
    if provisional:
        crit_list = sorted(provisional)
        total_prov = sum(provisional.values())
        issues.append({
            "id": "uncalibrated-judge",
            "title": "Some scores rely on an uncalibrated judge",
            "criterion_id": None,
            "category": "calibration",
            "category_label": _CATEGORY_LABEL["calibration"],
            "severity": "low",
            "impact_rank": 0.5,
            "why": (f"{total_prov} score{'s' if total_prov != 1 else ''} across "
                    f"{len(crit_list)} criterion"
                    f"{'a' if len(crit_list) != 1 else ''} were produced by a judge "
                    f"below the calibration threshold, so they're labelled "
                    f"PROVISIONAL. This is disclosed for rigor, not hidden — but "
                    f"treat the affected numbers as directional until the judge is "
                    f"calibrated against human labels."),
            "affected_n": total_prov,
            "n_measured": sum(agg[c]["measured"] for c in crit_list),
            "affected_share": None,
            "evidence": {
                "counts": {"provisional_scores": total_prov,
                           "criteria": len(crit_list)},
                "criteria": [{"criterion_id": c,
                              "description": (criteria_meta.get(c) or {}).get("description"),
                              "provisional": provisional[c]} for c in crit_list],
                "cases": [],
                "truncated": 0,
            },
            "suggested_fix": _fix("calibration"),
            "status": "open",
        })

    # rank worst-first: severity, then impact, then prevalence
    issues.sort(key=lambda i: (SEVERITY_RANK.get(i["severity"], 0),
                               i["impact_rank"], i["affected_n"]), reverse=True)

    summary = _summarize(scorecards, scored, errored, issues)
    return {"issues": issues, "summary": summary}


def _title_for(category: str, desc: str) -> str:
    """A short, scannable issue title from the category + criterion description."""
    prefix = {
        "safety": "Unsafe behavior", "prompt-injection": "Prompt-injection weakness",
        "tool-use": "Tool-use failure", "reliability": "Incorrect results",
        "efficiency": "Budget overrun", "calibration": "Provisional scoring",
        "eval-gaming": "Eval-gaming signal",
    }.get(category, "Failure")
    short = desc if len(desc) <= 70 else desc[:69] + "…"
    return f"{prefix}: {short}"


def _expected_str(expected: object) -> str:
    """Compact, human-readable form of a case's expected value."""
    if expected is None:
        return ""
    if isinstance(expected, dict):
        fo = expected.get("final_output")
        if fo is not None and len(expected) <= 4:
            return str(fo)
        import json
        return json.dumps(expected)
    return str(expected)


def _top_reasons(raw: list, k: int = 2) -> str:
    """The most common humanized error reasons among scoring errors."""
    from ascore.server.store import humanize_execution_error
    counts: dict[str, int] = {}
    for r in raw:
        msg = humanize_execution_error(r) if r else "Unknown error"
        counts[msg] = counts.get(msg, 0) + 1
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return "; ".join(f"{m} (×{n})" for m, n in top)


def _summarize(scorecards: list[dict], scored: list[dict], errored: list[dict],
               issues: list[dict]) -> dict:
    """Top-line: how many issues at each severity, the overall pass-rate + its
    Wilson interval (never a bare %), and an honest one-line headline."""
    by_sev = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for i in issues:
        by_sev[i["severity"]] = by_sev.get(i["severity"], 0) + 1

    n_scored = sum(sc.get("n_scored") or 0 for sc in scorecards) or len(scored)
    n_passed = sum(sc.get("n_passed") or 0 for sc in scorecards)
    # fall back to counting cases if scorecards didn't carry n_passed
    if not scorecards:
        n_passed = sum(1 for c in scored if c.get("passed"))
    pass_rate = (n_passed / n_scored) if n_scored else None
    wilson_low = min((sc.get("success_wilson_low") for sc in scorecards
                      if sc.get("success_wilson_low") is not None), default=None)
    wilson_high = max((sc.get("success_wilson_high") for sc in scorecards
                       if sc.get("success_wilson_high") is not None), default=None)

    top = issues[0] if issues else None
    if not issues:
        headline = ("No issues found — every scored case passed and no scoring "
                    "errors or provisional scores were recorded.")
    else:
        worst = top["severity"]
        headline = (f"{len(issues)} issue{'s' if len(issues) != 1 else ''} found — "
                    f"worst first, the top problem is a {worst}-severity "
                    f"{top['category_label'].lower()} issue.")

    return {
        "total_issues": len(issues),
        "by_severity": by_sev,
        "n_scored": n_scored,
        "n_passed": n_passed,
        "n_errored": len(errored),
        "pass_rate": round(pass_rate, 4) if pass_rate is not None else None,
        "pass_wilson_low": wilson_low,
        "pass_wilson_high": wilson_high,
        "headline": headline,
        "clean": len(issues) == 0,
    }
