"""Evidence-gated promotion + auto-demotion (SPEC-2 T28.3).

* ``evaluate_promotion`` — checks the config-driven criteria and NAMES every
  unmet one (observation hours, open-incident ceiling, clean window, tier/serve
  prereqs). Promotion may only advance one stage at a time.
* ``grant_promotion`` — refuses unless eligible (forced promotion impossible),
  then appends an append-only :class:`PromotionRecord` and recompiles the policy
  at the new stage.
* ``auto_demote_on_incident`` — an open S1/S2 immediately demotes the agent to
  the lowest stage and recompiles.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agenttic.registry.sqlite_store import NotFoundError
from agenttic.schema.release import (
    PromotionRecord,
    STAGE_ORDER,
    stage_rank,
    stages_from_config,
)

_SEV_RANK = {"S1": 0, "S2": 1, "S3": 2, "S4": 3}


@dataclass
class PromotionEvaluation:
    eligible: bool
    from_stage: str
    to_stage: str
    unmet: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def next_stage(cfg: dict, current: str) -> str | None:
    stages = stages_from_config(cfg)
    if current not in stages:
        return stages[0]
    i = stages.index(current)
    return stages[i + 1] if i + 1 < len(stages) else None


def _stage_since(reg, agent_id: str) -> datetime:
    """When the agent entered its current stage (last promotion record time, else
    its earliest dossier's creation)."""
    records = reg.list_promotion_records(agent_id)
    if records:
        try:
            return datetime.fromisoformat(records[-1]["created_at"])
        except Exception:  # noqa: BLE001
            pass
    try:
        return reg.latest_dossier(agent_id).created_at
    except NotFoundError:
        return _now()


def _promotion_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("release", {}).get("promotion", {})


def evaluate_promotion(reg, cfg: dict, agent_id: str, to_stage: str, *,
                       now: datetime | None = None) -> PromotionEvaluation:
    from agenttic.live.incidents import IncidentManager
    from agenttic.release.ladder import agent_stage

    now = now or _now()
    current = agent_stage(reg, agent_id)
    unmet: list[str] = []

    # one stage at a time
    expected_next = next_stage(cfg, current)
    if to_stage != expected_next:
        unmet.append(
            f"stage_order: can only advance {current}→{expected_next}, not {to_stage}")

    pcfg = _promotion_cfg(cfg)
    # observation hours for the target stage
    min_hours = float((pcfg.get("min_observation_hours", {}) or {}).get(to_stage, 0))
    observed = (now - _stage_since(reg, agent_id)).total_seconds() / 3600.0
    if observed < min_hours:
        unmet.append(
            f"observation_hours: {observed:.1f}h observed < {min_hours}h required")

    # open-incident ceiling: no open incident stricter than max_open_severity
    max_sev = pcfg.get("max_open_severity", "S3")
    mgr = IncidentManager(reg)
    for row in mgr.list_with_sla(cfg, agent_id=agent_id, now=now):
        if row["state"] == "closed":
            continue
        if _SEV_RANK.get(row["severity"], 9) < _SEV_RANK.get(max_sev, 9):
            unmet.append(
                f"open_incident: {row['incident_id']} ({row['severity']}) "
                f"stricter than ceiling {max_sev}")

    # tier / serve prereqs
    try:
        dossier = reg.latest_dossier(agent_id)
        req_tier = pcfg.get("required_tier")
        if req_tier and _tier_rank(dossier.tier_decision.tier) < _tier_rank(req_tier):
            unmet.append(f"tier: {dossier.tier_decision.tier} < required {req_tier}")
    except NotFoundError:
        unmet.append("tier: no dossier (agent is not certified)")

    return PromotionEvaluation(eligible=not unmet, from_stage=current,
                               to_stage=to_stage, unmet=unmet)


def _tier_rank(tier: str) -> int:
    return {"C": 0, "B": 1, "A": 2}.get(tier, -1)


class PromotionRefused(RuntimeError):
    """A promotion was refused because criteria were not met (no forced
    promotion — the message names every unmet criterion)."""


def grant_promotion(reg, cfg: dict, agent_id: str, cohort_id: str, to_stage: str,
                    *, granted_by: str, evidence_refs: list[str] | None = None,
                    now: datetime | None = None) -> PromotionRecord:
    """Grant a promotion IFF eligible; append the record and recompile."""
    ev = evaluate_promotion(reg, cfg, agent_id, to_stage, now=now)
    if not ev.eligible:
        raise PromotionRefused(
            f"promotion {ev.from_stage}→{to_stage} refused: " + "; ".join(ev.unmet))
    record = PromotionRecord(
        record_id=f"prom-{uuid.uuid4().hex[:12]}", agent_id=agent_id,
        cohort_id=cohort_id, from_stage=ev.from_stage, to_stage=to_stage,
        kind="promotion", granted_by=granted_by,
        evidence_refs=list(evidence_refs or []),
        reason=f"criteria met for {to_stage}")
    reg.append_promotion_record(record)
    _recompile_at_stage(reg, cfg, agent_id, to_stage)
    return record


def auto_demote_on_incident(reg, cfg: dict, agent_id: str, *,
                            now: datetime | None = None) -> PromotionRecord | None:
    """If an S1/S2 incident is open, demote to the lowest stage immediately and
    recompile. Returns the demotion record or None."""
    from agenttic.live.incidents import IncidentManager
    from agenttic.release.ladder import agent_stage

    mgr = IncidentManager(reg)
    open_crit = [r for r in mgr.list_with_sla(cfg, agent_id=agent_id, now=now)
                 if r["state"] != "closed" and r["severity"] in ("S1", "S2")]
    if not open_crit:
        return None
    current = agent_stage(reg, agent_id)
    lowest = stages_from_config(cfg)[0]
    if current == lowest:
        return None  # already at the floor
    record = PromotionRecord(
        record_id=f"demo-{uuid.uuid4().hex[:12]}", agent_id=agent_id,
        cohort_id="", from_stage=current, to_stage=lowest, kind="demotion",
        granted_by="system",
        evidence_refs=[f"incident:{r['incident_id']}" for r in open_crit],
        reason=f"open {open_crit[0]['severity']} incident → auto-demote")
    reg.append_promotion_record(record)
    _recompile_at_stage(reg, cfg, agent_id, lowest)
    try:
        from agenttic.feeds.webhooks import STAGE_DEMOTION, enqueue_webhook
        enqueue_webhook(reg, cfg, STAGE_DEMOTION, agent_id,
                        {"from_stage": current, "to_stage": lowest})
    except Exception:  # noqa: BLE001 — feeds optional
        pass
    return record


def _recompile_at_stage(reg, cfg: dict, agent_id: str, stage: str) -> None:
    """Recompile the agent's policy carrying the new stage dimension."""
    try:
        from agenttic.certification.staleness import status as compute_status
        from agenttic.enforce.compiler import compile_policy

        dossier = reg.latest_dossier(agent_id)
        try:
            card = reg.get_card(agent_id)
        except NotFoundError:
            card = None
        from agenttic.live.incidents import IncidentManager
        incidents = IncidentManager(reg).list_with_sla(cfg, agent_id=agent_id)
        status = compute_status(reg, dossier)
        policy = compile_policy(dossier, card, incidents, cfg, status=status,
                                stage=stage)
        try:
            current = reg.latest_policy(agent_id)
            if current.content_hash == policy.content_hash:
                return
        except NotFoundError:
            pass
        reg.save_policy(policy)
    except Exception:  # noqa: BLE001 — enforcement optional
        pass
