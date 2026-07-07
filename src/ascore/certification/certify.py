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

from ascore import ops
from ascore.certification.coverage import coverage as compute_coverage
from ascore.certification.dossier import assemble
from ascore.certification.elicitation import analyze_elicitation, run_matrix
from ascore.certification.profiles import load_profile, seed_profile
from ascore.certification.tiers import decide
from ascore.schema.certification import Attestation


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


def attestation_mode_for(caller_role: str | None) -> str:
    """Attestation is COMPUTED from the caller's principal, never selected
    (Hard Rule 13): an independent evaluator principal ⇒ ``independent``; the
    agent's own owner ⇒ ``self_attested``."""
    from ascore.server.auth import is_evaluator
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
    from ascore.budget import BudgetExceededError, check_pre_run
    from ascore.cost import estimate_for_run

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
        from ascore.metrics.standard_suites import canonical_suite_ids
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
    from ascore.metrics.redteam import seed_redteam_injection_suite
    from ascore.metrics.safety_suite import seed_safety_content_suite
    from ascore.metrics.standard_suites import seed_standard_suites

    seed_standard_suites(reg)
    seed_redteam_injection_suite(reg)
    seed_safety_content_suite(reg)
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

    tier_decision = decide(
        profile=profile, components=components, coverage=cov,
        judge_calibrated=calibrated, elicitation_analysis=analysis,
        evidence_refs=evidence_refs, cfg=cfg)

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

    return CertifyResult(dossier=dossier, cost_usd=round(cost, 6), cached=False,
                         elicitation=analysis.summary())
