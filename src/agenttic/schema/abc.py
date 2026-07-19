"""ABC scorecard schema (SPEC-6 Step 26.1).

We score every approved suite against the applicable items of the Agentic
Benchmark Checklist (Zhu et al., arXiv:2507.02825), computed from evidence we
already hold. Items we cannot evidence score N/A (``score=None``) and are shown
as such — never estimated upward (Hard Rule 30). The headline "benchmark rigor"
is the mean of the scored items.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ABCCategory = Literal["I", "II", "III"]
ABCStatus = Literal["computed", "entered", "n/a"]


class ABCItem(BaseModel):
    """One checklist item and how we scored it."""

    item_id: str          # e.g. "I.a"
    name: str
    category: ABCCategory  # I task validity · II outcome validity · III reporting
    score: float | None    # 0..1, or None when we have no evidence (shown N/A)
    status: ABCStatus      # computed from data · entered by a human · n/a
    evidence: str


class ABCReport(BaseModel):
    """Benchmark-rigor scorecard for one suite version."""

    suite_id: str
    version: int
    items: list[ABCItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def overall(self) -> float | None:
        """Mean of the scored items; None if nothing could be evidenced. N/A
        items are excluded, never counted as zero (that would penalise honesty)."""
        scored = [i.score for i in self.items if i.score is not None]
        return round(sum(scored) / len(scored), 4) if scored else None
