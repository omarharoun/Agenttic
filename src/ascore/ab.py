"""A/B testing — run two agent variants head-to-head on one suite and produce a
statistically honest verdict.

This is a thin orchestration layer over the existing pipeline: each variant is
run + scored + aggregated through the very same ops (``run_and_score_op``) the
single-agent flow uses, so the harness, scoring, judge separation, registry,
checkpoint/resume and BYO-key plumbing all apply unchanged. The only new work
is the *paired comparison* of the two resulting scorecards (see
:func:`compare_scorecards`) and the significance tests in :mod:`ascore.stats`.

Pairing is by ``test_id``: a case counts toward the comparison only when **both**
variants scored it (neither errored). Cases that errored in either variant are
excluded and listed — consistent with the errored-vs-failed distinction in the
scorecard aggregate, so a scoring outage can't masquerade as a variant losing.
"""

from __future__ import annotations

import uuid
from typing import Callable

from ascore import ops
from ascore.registry.sqlite_store import Registry
from ascore.schema.ab import (
    ABComparison,
    ABVariant,
    CriterionComparison,
    FlippedCase,
)
from ascore.schema.scorecard import Scorecard
from ascore.stats import mcnemar, paired_bootstrap

ProgressFn = Callable[[str, dict], None]


def effective_agent_ids(variant_a: ABVariant, variant_b: ABVariant
                        ) -> tuple[str, str]:
    """The agent_ids the two runs store traces and scorecards under. Distinct
    base ids pass through; when both variants share an agent_id (the
    same-agent/different-prompt or different-model case) they're suffixed with
    the variant label so the two runs stay fully isolated and their scorecards
    remain individually addressable."""
    a, b = variant_a.agent_id, variant_b.agent_id
    if a == b:
        return f"{a}::{variant_a.label}", f"{b}::{variant_b.label}"
    return a, b


def _adapter_for(cfg: dict, variant: ABVariant, agent_id: str, client):
    return ops.build_adapter(
        cfg, variant=variant.variant, agent_id=agent_id,
        url=variant.url, managed_agent_id=variant.managed_agent_id,
        environment_id=variant.environment_id, client=client,
        system_prompt=variant.system_prompt, model=variant.model,
        headers=variant.headers or None,
        cost_per_call_usd=variant.cost_per_call_usd,
        expected_input_tokens=variant.expected_input_tokens,
        expected_output_tokens=variant.expected_output_tokens)


async def _run_variant(cfg: dict, reg: Registry, suite_id: str,
                       version: int | None, variant: ABVariant, agent_id: str,
                       clients: dict, on_progress: ProgressFn | None) -> Scorecard:
    adapter = _adapter_for(cfg, variant, agent_id, clients.get("agent"))
    return await ops.run_and_score_op(
        cfg, reg, adapter, suite_id, version, on_progress,
        judge_client=clients.get("judge"))


def _tag(tag: str, fn: ProgressFn | None) -> ProgressFn | None:
    """Wrap a progress callback so each event is labeled with the variant it
    belongs to (the UI shows A/B progress separately)."""
    if fn is None:
        return None
    return lambda t, d: fn(t, {**d, "variant": tag})


async def run_ab_op(
    cfg: dict,
    reg: Registry,
    suite_id: str,
    variant_a: ABVariant,
    variant_b: ABVariant,
    *,
    version: int | None = None,
    on_progress: ProgressFn | None = None,
    clients: dict | None = None,
    clients_a: dict | None = None,
    clients_b: dict | None = None,
    comparison_id: str | None = None,
    persist: bool = True,
) -> ABComparison:
    """Run both variants on the same suite, then compare. ``clients`` is the
    tenant's shared client set used for both runs (same judge for a fair
    comparison); ``clients_a``/``clients_b`` override the agent client per
    variant (a test seam — production passes one shared ``clients``)."""
    ca = clients_a or clients or {}
    cb = clients_b or clients or {}
    eff_a, eff_b = effective_agent_ids(variant_a, variant_b)

    sc_a = await _run_variant(cfg, reg, suite_id, version, variant_a, eff_a, ca,
                              _tag("A", on_progress))
    sc_b = await _run_variant(cfg, reg, suite_id, version, variant_b, eff_b, cb,
                              _tag("B", on_progress))

    comparison = compare_scorecards(
        comparison_id or uuid.uuid4().hex[:12], sc_a, sc_b, variant_a, variant_b)
    if persist:
        reg.save_ab_comparison(comparison)
    return comparison


def _passed_by_id(sc: Scorecard) -> dict[str, bool]:
    return {r.test_id: r.passed for r in sc.run_scores if r.scoring_error is None}


def _crit_scores_by_id(sc: Scorecard) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in sc.run_scores:
        if r.scoring_error is not None:
            continue
        out[r.test_id] = {s.criterion_id: s.score for s in r.criterion_scores}
    return out


def compare_scorecards(
    comparison_id: str,
    sc_a: Scorecard,
    sc_b: Scorecard,
    variant_a: ABVariant,
    variant_b: ABVariant,
) -> ABComparison:
    """Build the paired comparison from two scorecards over the same suite.

    Headline success rates are computed over the **paired** subset (cases scored
    by both variants), so A and B share an identical denominator — the
    statistically honest basis for McNemar's test."""
    a_pass = _passed_by_id(sc_a)
    b_pass = _passed_by_id(sc_b)
    a_all = {r.test_id for r in sc_a.run_scores}
    b_all = {r.test_id for r in sc_b.run_scores}

    paired = sorted(set(a_pass) & set(b_pass))            # scored by both
    excluded = sorted((a_all | b_all) - set(paired))      # errored or one-sided

    pa = [a_pass[t] for t in paired]
    pb = [b_pass[t] for t in paired]
    n = len(paired)
    rate_a = (sum(pa) / n) if n else 0.0
    rate_b = (sum(pb) / n) if n else 0.0
    mc = mcnemar(pa, pb)

    # per-criterion paired bootstrap over the cases both variants scored
    a_crit = _crit_scores_by_id(sc_a)
    b_crit = _crit_scores_by_id(sc_b)
    crit_ids = sorted(set(sc_a.per_criterion_means) | set(sc_b.per_criterion_means))
    per_criterion: list[CriterionComparison] = []
    for cid in crit_ids:
        xs, ys = [], []
        for t in paired:
            if cid in a_crit.get(t, {}) and cid in b_crit.get(t, {}):
                xs.append(a_crit[t][cid])
                ys.append(b_crit[t][cid])
        if not xs:
            continue
        bs = paired_bootstrap(xs, ys)
        per_criterion.append(CriterionComparison(
            criterion_id=cid, mean_a=bs.mean_a, mean_b=bs.mean_b,
            delta=bs.delta, direction=bs.direction, p_value=bs.p_value,
            ci_low=bs.ci_low, ci_high=bs.ci_high, significant=bs.significant,
            n=bs.n))

    flipped = [
        FlippedCase(test_id=t, a_passed=a_pass[t], b_passed=b_pass[t],
                    direction="gain" if b_pass[t] else "loss")
        for t in paired if a_pass[t] != b_pass[t]
    ]

    winner, verdict = _verdict(variant_a, variant_b, rate_a, rate_b, mc, n)

    return ABComparison(
        comparison_id=comparison_id,
        suite_id=sc_a.suite_id, suite_version=sc_a.suite_version,
        rubric_id=sc_a.rubric_id, rubric_version=sc_a.rubric_version,
        label_a=variant_a.label, label_b=variant_b.label,
        variant_a=variant_a, variant_b=variant_b,
        scorecard_a_id=sc_a.scorecard_id, scorecard_b_id=sc_b.scorecard_id,
        n_paired=n, excluded_test_ids=excluded,
        success_rate_a=rate_a, success_rate_b=rate_b,
        success_delta=rate_b - rate_a, mcnemar=mc.to_dict(),
        per_criterion=per_criterion, flipped_cases=flipped,
        mean_cost_a=sc_a.mean_cost_usd, mean_cost_b=sc_b.mean_cost_usd,
        total_cost_a=sc_a.total_cost_usd, total_cost_b=sc_b.total_cost_usd,
        p95_latency_a=sc_a.p95_latency_ms, p95_latency_b=sc_b.p95_latency_ms,
        winner=winner, verdict=verdict)


def _verdict(variant_a: ABVariant, variant_b: ABVariant, rate_a: float,
             rate_b: float, mc, n: int) -> tuple[str, str]:
    """The headline call: a clear winner only when the paired test is
    significant; otherwise an explicit 'no significant difference', with a
    small-sample caveat when the data simply can't support a conclusion."""
    la, lb = variant_a.label, variant_b.label
    p = mc.p_value

    def _pct(x):
        return f"{100 * x:.0f}%"

    rates = (f"{la} {_pct(rate_a)} vs {lb} {_pct(rate_b)} on {n} paired case(s)")

    if n == 0:
        return "tie", ("No paired cases to compare — every case errored in at "
                       "least one variant. Fix the scoring config and re-run.")

    if mc.significant:
        winner = "B" if mc.favors == "B" else "A"
        wlabel, llabel = (lb, la) if winner == "B" else (la, lb)
        return winner, (f"{wlabel} beats {llabel} on the suite "
                        f"(significant, McNemar p={p:.3f}, n={n}). {rates}.")

    if mc.underpowered:
        return "tie", (f"No significant difference — the sample is too small to "
                       f"conclude ({mc.n_discordant} case(s) differ out of {n}; "
                       f"McNemar p={p:.2f}). Add more cases to decide. {rates}.")

    return "tie", (f"No significant difference between {la} and {lb} "
                   f"(McNemar p={p:.2f}, n={n}). {rates}.")
