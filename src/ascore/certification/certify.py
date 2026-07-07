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


def judge_is_calibrated(cfg: dict) -> bool:
    """Whether the judge is calibrated for certification purposes.

    The shipped demonstrated-judge corpus is explicitly labeled
    *demonstrated-but-provisional-grade* (a small clear-cut seed sample), so for
    certification we treat the judge as **provisional** — which caps the tier at
    B (Hard Rule 11). A full independent judge calibration would flip this."""
    return False


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
    attestation_mode: str = "self_attested",
    tenant: str | None = None,
    on_progress=None,
    force: bool = False,
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

    k = int(k or profile.min_k)
    suite_ids = [ref.suite_id for ref in profile.suite_refs]
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
        attestation=Attestation(mode=attestation_mode, tenant=tenant or reg.tenant),
        scorecard_refs=evidence_refs,
        calibration=calibration, elicitation=analysis.summary(),
        inspect_log_ref=f"inspect:{neutral.get('run_id')}",
        persist=True)

    return CertifyResult(dossier=dossier, cost_usd=round(cost, 6), cached=False,
                         elicitation=analysis.summary())
