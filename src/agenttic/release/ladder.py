"""Stage-gated access (SPEC-2 T28.2).

* ``agent_stage`` — how far an agent has been promoted (the max stage across its
  cohorts; default ``internal``).
* ``resolve_caller_stage`` — the stage a caller's cohort maps to.
* ``stage_gate`` — a caller whose cohort stage is ABOVE the agent's promoted
  stage is denied (``origin=stage_gate``): the agent hasn't been released that far.

The policy compiler gains a stage dimension: a higher-exposure stage's posture is
**stricter-or-equal** to a lower one (tighten-only holds across stages).
"""

from __future__ import annotations

from dataclasses import dataclass

from agenttic.registry.sqlite_store import NotFoundError
from agenttic.schema.release import stage_rank, stages_from_config

STAGE_GATE_ORIGIN = "stage_gate"


def agent_stage(reg, agent_id: str) -> str:
    """The agent's current promoted stage — the latest stage reached on its
    promotion track (folded from the append-only promotion records). Defaults to
    the lowest stage before any promotion."""
    records = reg.list_promotion_records(agent_id)
    stage = "internal"
    for r in records:  # chronological (append order)
        stage = r.get("to_stage", stage)
    return stage


def resolve_caller_stage(reg, cohort_id: str | None) -> str | None:
    if not cohort_id:
        return None
    try:
        return reg.get_cohort(cohort_id).stage
    except NotFoundError:
        return None


@dataclass
class StageGate:
    allowed: bool
    caller_stage: str | None
    agent_stage: str
    reason: str = ""


def stage_gate(reg, agent_id: str, caller_cohort_id: str | None) -> StageGate:
    """Resolve the caller's stage and gate it against the agent's promoted stage.
    An unknown cohort defaults to the lowest stage (least privilege)."""
    served = agent_stage(reg, agent_id)
    caller = resolve_caller_stage(reg, caller_cohort_id)
    effective = caller or "internal"
    if stage_rank(effective) > stage_rank(served):
        return StageGate(
            allowed=False, caller_stage=caller, agent_stage=served,
            reason=(f"caller stage {effective} is above the agent's promoted "
                    f"stage {served}"))
    return StageGate(allowed=True, caller_stage=caller, agent_stage=served)


# --------------------------------------------------------------------------- #
# Compiler stage dimension.
# --------------------------------------------------------------------------- #


def stage_posture(cfg: dict, stage: str) -> dict:
    """The tightening a stage adds, config-driven with a monotonic default.

    Higher-exposure stages are stricter-or-equal: each stage's tightening is a
    superset of the lower stages', so a GA policy can only be tighter than a
    vetted one (tighten-only across stages, Rule 20/24)."""
    scfg = (cfg or {}).get("enforcement", {}).get("compiler", {}).get(
        "stage_posture")
    if scfg and stage in scfg:
        return dict(scfg[stage])
    # built-in monotonic default
    defaults = {
        "internal": {},
        "vetted": {},
        "limited": {"approvals": "write"},
        "ga": {"approvals": "write", "lane3_sampling": 0.25},
    }
    return defaults.get(stage, {})


def apply_stage_to_posture(posture, cfg: dict, stage: str | None) -> None:
    """Tighten a compiler posture in place for the given stage (no-op for None)."""
    if not stage:
        return
    sp = stage_posture(cfg, stage)
    origin = f"stage:{stage}"
    if sp.get("serve") == "deny":
        posture.deny(origin)
    if sp.get("approvals") == "write":
        posture.require("write", origin)
    if "lane3_sampling" in sp:
        posture.sample(sp["lane3_sampling"], origin)
