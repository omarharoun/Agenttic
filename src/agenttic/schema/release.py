"""Staged release ladder (SPEC-2 M14, T28.1).

An agent is served through ordered release stages — ``internal`` → ``vetted`` →
``limited`` → ``ga`` — with tightening posture as it climbs. A :class:`Cohort`
maps a set of callers to a stage; :class:`PromotionCriteria` are the
evidence-gates to advance; :class:`PromotionRecord` is the append-only audit of a
grant (or auto-demotion).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Stage = Literal["internal", "vetted", "limited", "ga"]

# canonical order (index = strictness rank; ga is strictest / most-exposed)
STAGE_ORDER: tuple[str, ...] = ("internal", "vetted", "limited", "ga")


def stage_rank(stage: str) -> int:
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1


def stages_from_config(cfg: dict) -> tuple[str, ...]:
    stages = (cfg or {}).get("release", {}).get("stages")
    return tuple(stages) if stages else STAGE_ORDER


class Cohort(BaseModel):
    """A set of callers served at a given stage."""

    cohort_id: str
    agent_id: str
    stage: Stage
    members: list[str] = Field(default_factory=list)  # caller ids / cohort tokens
    description: str = ""

    def ref(self) -> str:
        return f"cohort:{self.cohort_id}"


class PromotionCriteria(BaseModel):
    """Evidence-gates required to advance to ``to_stage``."""

    to_stage: Stage
    min_observation_hours: float = 0.0
    max_open_severity: str = "S3"       # any open incident stricter than this blocks
    clean_window_hours: float = 0.0     # no new incident within this trailing window
    required_tier: str | None = None    # e.g. "B" — dossier tier must be ≥ this
    required_serve: str = "allow"       # posture must be serving (not deny)


class PromotionRecord(BaseModel):
    """Append-only record of a stage change (promotion or auto-demotion)."""

    record_id: str
    agent_id: str
    cohort_id: str
    from_stage: Stage
    to_stage: Stage
    kind: Literal["promotion", "demotion"] = "promotion"
    granted_by: str = ""                 # PAT identity, or "system" for auto-demotion
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _valid_transition(self) -> "PromotionRecord":
        if self.from_stage == self.to_stage:
            raise ValueError("promotion record must change stage")
        return self

    def ref(self) -> str:
        return f"promotion:{self.record_id}"
