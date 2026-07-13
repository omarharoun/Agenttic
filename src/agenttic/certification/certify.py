"""End-to-end certification pipeline (SPEC-2 T14.5).

``certify()`` ties the M4/M5 pieces together into one evidence dossier:

1. seed the standard suites + the profile, build the agent adapter;
2. run the elicitation matrix (neutral + strong) over the harness + result cache;
3. persist each config's canonical run so every number resolves to a persisted id;
4. compute domain coverage + the elicitation analysis;
5. decide the tier (pure, config-driven) — a provisional judge caps it at B;
6. assemble + persist the hash-chained dossier.

**Cache-aware:** if a dossier already exists for this agent's *exact*
config-hash + profile, it is returned unchanged for **$0** (no re-run).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from agenttic import ops
from agenttic.certification.coverage import coverage as compute_coverage
from agenttic.certification.dossier import assemble
from agenttic.certification.elicitation import analyze_elicitation, run_matrix
from agenttic.certification.profiles import load_profile, seed_profile
from agenttic.certification.tiers import decide
from agenttic.schema.certification import Attestation


@dataclass
class CertifyResult:
    dossier: object
    cost_usd: float
    cached: bool
    elicitation: dict


class CertificationAborted(RuntimeError):
    """The certification was aborted mid-run (e.g. the caller's PAT was revoked).
    No dossier is written and no reusable-dossier cache entry is created, so a
    later certify simply re-runs — the abort never poisons the cache."""


async def renew(cfg: dict, reg, *, agent_id: str, profile_id: str,
                adapter=None, variant: str = "reference", url: str = "",
                system_prompt: str = "", client=None, judge_client=None,
                caller_role: str | None = None, **kw) -> CertifyResult:
    """Renew an agent's certification, producing a NEW dossier chained to the
    previous one.

    * **Unchanged agent** (same config hash) ⇒ **$0**: reuse the prior dossier's
      evidence and tier verbatim, emit a fresh chained dossier (no re-run).
    * **Changed agent** ⇒ re-certify (chained), and attach a **case-level diff**
      vs the previous run (reusing the regression stats machinery).
    """
    from agenttic.certification.dossier import assemble
    from agenttic.certification.profiles import load_profile, seed_profile
    from agenttic.schema.certification import Attestation

    seed_profile(cfg, reg, profile_id)
    profile = load_profile(cfg, reg, profile_id)
    if adapter is None:
        adapter = ops.build_adapter(cfg, variant=variant, agent_id=agent_id,
                                    url=url, system_prompt=system_prompt,
                                    client=client)
    base_hash = adapter.config_hash()

    prev = _find_reusable_dossier(reg, agent_id, base_hash, profile_id)
    if prev is not None:
        # unchanged agent → $0, identical tier, chained dossier
        dossier = assemble(
            reg, agent_id=agent_id, agent_config_hash=base_hash, profile=profile,
            tier_decision=prev.tier_decision, coverage=prev.coverage,
            attestation=Attestation(mode=attestation_mode_for(caller_role),
                                    tenant=reg.tenant),
            scorecard_refs=prev.scorecard_refs, calibration=prev.calibration,
            elicitation=prev.elicitation, inspect_log_ref=prev.inspect_log_ref,
            prev_dossier_sha256=prev.content_sha256, persist=True)
        reg.append_dossier_event(dossier.dossier_id, agent_id, "renewed",
                                 reason="unchanged agent — cache hit ($0)")
        return CertifyResult(dossier=dossier, cost_usd=0.0, cached=True,
                             elicitation=prev.elicitation or {})

    # changed agent → full re-certify (force new dossier), then diff
    res = await certify(cfg, reg, agent_id=agent_id, profile_id=profile_id,
                        adapter=adapter, client=client, judge_client=judge_client,
                        caller_role=caller_role, force=True, **kw)
    reg.append_dossier_event(res.dossier.dossier_id, agent_id, "renewed",
                             reason="agent changed — re-certified")
    return res


def case_level_diff(prev_run: dict, new_run: dict) -> dict:
    """Case-level pass-rate diff between two canonical runs, reusing the paired
    regression machinery (McNemar over per-case pass vectors when available)."""
    from agenttic.stats import mcnemar
    p, n = prev_run.get("per_case", {}), new_run.get("per_case", {})
    common = sorted(set(p) & set(n))
    a = [1.0 if p[t] and all(p[t]) else 0.0 for t in common]
    b = [1.0 if n[t] and all(n[t]) else 0.0 for t in common]
    result = {"n_cases": len(common),
              "regressions": [t for t, (x, y) in zip(common, zip(a, b)) if x > y],
              "improvements": [t for t, (x, y) in zip(common, zip(a, b)) if y > x]}
    if common:
        mc = mcnemar([bool(x) for x in a], [bool(x) for x in b])
        result["mcnemar"] = {"p_value": round(getattr(mc, "p_value", 1.0), 4),
                             "favors": getattr(mc, "favors", "tie")}
    return result


def attestation_mode_for(caller_role: str | None) -> str:
    """Attestation is COMPUTED from the caller's principal, never selected
    (Hard Rule 13): an independent evaluator principal ⇒ ``independent``; the
    agent's own owner ⇒ ``self_attested``."""
    from agenttic.server.auth import is_evaluator
    return "independent" if is_evaluator(caller_role) else "self_attested"


def judge_is_calibrated(cfg: dict) -> bool:
    """Whether the judge is calibrated for certification purposes.

    The shipped demonstrated-judge corpus is explicitly labeled
    *demonstrated-but-provisional-grade* (a small clear-cut seed sample), so for
    certification we treat the judge as **provisional** — which caps the tier at
    B (Hard Rule 11). A full independent judge calibration would flip this."""
    return False


def _enforce_certification_ceiling(cfg: dict, reg, *, n_configs: int,
                                   n_suites: int, k: int) -> None:
    """Gate the run against the tenant spend cap using the cost estimator. No-op
    when caps are 0 (unlimited) or no suites/estimator context exists."""
    from agenttic.budget import BudgetExceededError, check_pre_run
    from agenttic.cost import estimate_for_run

    # hard ceiling: if the tenant is already at/over its daily cap, refuse before
    # spending anything more (BYO-key evaluators can't exceed their own cap).
    daily_cap = float((cfg.get("budget", {}) or {}).get("max_daily_cost_usd", 0) or 0)
    if daily_cap and reg is not None and reg.spend_today() >= daily_cap:
        raise BudgetExceededError(
            f"daily spend cap ${daily_cap:.4f} already reached "
            f"(spent ${reg.spend_today():.4f}); certification refused")

    projected = 0.0
    # estimate per-suite cost; multiply by configs (neutral+strong) × k
    for ref_id in _suite_ids_from_cfg(cfg, reg):
        try:
            est = estimate_for_run(cfg, reg, ref_id)
            projected += float(getattr(est, "projected_usd", 0.0))
        except Exception:  # noqa: BLE001 — estimator is best-effort
            continue
    projected *= max(1, n_configs) * max(1, k)
    if projected > 0:
        check_pre_run(cfg, reg, projected)  # raises BudgetExceededError if over cap


def _suite_ids_from_cfg(cfg, reg):
    try:
        from agenttic.metrics.standard_suites import canonical_suite_ids
        return canonical_suite_ids(reg)
    except Exception:  # noqa: BLE001
        return []


def _find_reusable_dossier(reg, agent_id: str, agent_config_hash: str,
                           profile_id: str):
    """An existing dossier for this exact (agent_config_hash, profile) — the
    cache hit. Returns the Dossier or None."""
    for row in reg.list_dossiers(agent_id):
        if row["profile_id"] != profile_id:
            continue
        d = reg.get_dossier(row["dossier_id"])
        if d.agent_config_hash == agent_config_hash:
            return d
    return None


async def certify(
    cfg: dict, reg, *,
    agent_id: str,
    profile_id: str,
    adapter=None,
    variant: str = "reference",
    url: str = "",
    system_prompt: str = "",
    model: str = "",
    client=None,
    judge_client=None,
    faithfulness_checker=None,
    fi_evaluate_fn=None,
    k: int | None = None,
    caller_role: str | None = None,
    tenant: str | None = None,
    on_progress=None,
    force: bool = False,
    abort_check=None,
) -> CertifyResult:
    from agenttic.metrics.redteam import seed_redteam_injection_suite
    from agenttic.metrics.safety_suite import seed_safety_content_suite
    from agenttic.metrics.standard_suites import seed_standard_suites
    from agenttic.metrics.swe_suites import seed_swe_suites

    seed_standard_suites(reg)
    seed_redteam_injection_suite(reg)
    seed_safety_content_suite(reg)
    seed_swe_suites(reg)  # cert-swe-v1 pack authored suites (idempotent)
    profile = seed_profile(cfg, reg, profile_id)
    profile = load_profile(cfg, reg, profile_id)

    if adapter is None:
        adapter = ops.build_adapter(cfg, variant=variant, agent_id=agent_id,
                                    url=url, system_prompt=system_prompt,
                                    model=model, client=client)
    base_hash = adapter.config_hash()

    # -- cache: identical config + profile → reuse, $0 -----------------------
    if not force:
        reused = _find_reusable_dossier(reg, agent_id, base_hash, profile_id)
        if reused is not None:
            return CertifyResult(dossier=reused, cost_usd=0.0, cached=True,
                                 elicitation=reused.elicitation or {})

    def _check_abort(stage: str):
        if abort_check is not None and abort_check():
            raise CertificationAborted(
                f"certification of {agent_id} aborted at {stage} "
                f"(caller no longer authorized) — no dossier written")

    _check_abort("start")
    k = int(k or profile.min_k)
    suite_ids = [ref.suite_id for ref in profile.suite_refs]

    # BYO-key billing ceiling (SPEC-2 T15.5): enforce the tenant's spend cap
    # up-front. For an independent evaluator this is *their* key + *their*
    # ceiling — certification never spends past it. No-op when caps are 0
    # (unlimited). A rough projection (per config × per suite × k) gates the run;
    # per-suite RunBudget still aborts mid-run if the per-run cap is crossed.
    _enforce_certification_ceiling(cfg, reg, n_configs=2, n_suites=len(suite_ids), k=k)

    matrix = await run_matrix(
        cfg, reg, adapter, k=k, suite_ids=suite_ids or None,
        judge_client=judge_client or client, fi_evaluate_fn=fi_evaluate_fn,
        faithfulness_checker=faithfulness_checker, on_progress=on_progress)

    # persist each config's canonical run so evidence refs resolve to ids
    evidence_refs: list[str] = []
    cost = 0.0
    for name, result in matrix["configs"].items():
        run_id = result["run_id"]
        reg.save_canonical_run(run_id, agent_id, json.dumps(result))
        evidence_refs.append(f"canonical:{run_id}")
        cost += float(result.get("k_runs_cost_usd", 0.0))
    for ref in profile.suite_refs:
        evidence_refs.append(ref.ref())

    # bill the certification run to THIS tenant's spend ledger (the BYO key that
    # served the judge + agent calls) so the evaluator's spend is attributable.
    if cost:
        reg.record_spend(cfg.get("models", {}).get("judge_strong", "judge"), cost)

    # abort before any dossier is assembled/persisted — leaves no dossier and no
    # reusable-dossier cache entry (the canonical runs are harmless evidence).
    _check_abort("assembly")

    neutral = matrix["configs"].get("neutral", {})
    components = neutral.get("components", {})
    analysis = analyze_elicitation(matrix, cfg)
    cov = compute_coverage(reg, profile)
    calibrated = judge_is_calibrated(cfg)

    # documentation prerequisite (T21.2): a covered agent must be documented
    # (autonomy classified + a card present), else the tier is capped at B.
    from agenttic.cards.agency import detect_covered_agent
    from agenttic.cards.autonomy import classify_autonomy
    autonomy = classify_autonomy(reg, agent_id, cfg)
    covered = detect_covered_agent(reg, agent_id, cfg).covered
    try:
        reg.get_card(agent_id)
        has_card = True
    except Exception:  # noqa: BLE001
        has_card = False

    tier_decision = decide(
        profile=profile, components=components, coverage=cov,
        judge_calibrated=calibrated, elicitation_analysis=analysis,
        evidence_refs=evidence_refs, cfg=cfg,
        autonomy_level=autonomy.level, covered_agent=covered, has_card=has_card)

    reg.save_elicitation_summary(agent_id, analysis.summary())

    calibration = {
        "judge_calibrated": calibrated,
        "calibration_mode": neutral.get("calibration_mode"),
        "ece": neutral.get("ece"),
    }

    dossier = assemble(
        reg, agent_id=agent_id, agent_config_hash=base_hash, profile=profile,
        tier_decision=tier_decision, coverage=cov,
        attestation=Attestation(mode=attestation_mode_for(caller_role),
                                tenant=tenant or reg.tenant),
        scorecard_refs=evidence_refs,
        calibration=calibration, elicitation=analysis.summary(),
        inspect_log_ref=f"inspect:{neutral.get('run_id')}",
        persist=True)

    # evidence changed → recompile the enforcement policy from the new dossier
    try:
        from agenttic.enforce.compiler import recompile_for_agent
        recompile_for_agent(reg, cfg, agent_id)
    except Exception:  # noqa: BLE001 — enforcement is optional for a bare certify
        pass

    # webhook on a tier change vs the previous dossier
    try:
        prior = [d for d in reg.list_dossiers(agent_id)
                 if d["dossier_id"] != dossier.dossier_id]
        prev_tier = reg.get_dossier(prior[-1]["dossier_id"]).tier_decision.tier \
            if prior else None
        if prev_tier is not None and prev_tier != tier_decision.tier:
            from agenttic.feeds.webhooks import TIER_CHANGE, enqueue_webhook
            enqueue_webhook(reg, cfg, TIER_CHANGE, agent_id,
                            {"from_tier": prev_tier, "to_tier": tier_decision.tier})
    except Exception:  # noqa: BLE001 — feeds optional
        pass

    return CertifyResult(dossier=dossier, cost_usd=round(cost, 6), cached=False,
                         elicitation=analysis.summary())
