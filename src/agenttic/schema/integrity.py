"""Suite-integrity report schema (SPEC-6 Step 25).

The three mechanical gates — oracle (solvability), dummy (non-vacuity), exploit
(cheat-resistance) — produce one :class:`GateResult` each. A suite cannot be
approved until every gate is *clear*: it either passed, or was explicitly waived
with a recorded reason (Hard Rule 27). The report is stored per suite version.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

GateName = Literal["oracle", "dummy", "exploit"]
GATE_NAMES: tuple[GateName, ...] = ("oracle", "dummy", "exploit")


class GateResult(BaseModel):
    """The outcome of one integrity gate over one suite version."""

    gate: GateName
    ran: bool               # False when the gate could not run (e.g. no model)
    passed: bool            # meaningful only when ran
    #: cases that FAILED the gate's expectation (broken oracle / vacuous /
    #: exploited), each blocking approval until fixed or waived
    failing_case_ids: list[str] = Field(default_factory=list)
    detail: str = ""
    #: per-case notes the gate could not decide mechanically (e.g. judge-only)
    inconclusive_case_ids: list[str] = Field(default_factory=list)
    waived: bool = False
    waiver_reason: str | None = None

    @property
    def clear(self) -> bool:
        """Passable for approval: waived, or ran-and-passed."""
        return self.waived or (self.ran and self.passed)


class IntegrityReport(BaseModel):
    """All three gate results for one suite version."""

    suite_id: str
    version: int
    gates: list[GateResult] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get(self, name: GateName) -> GateResult | None:
        return next((g for g in self.gates if g.gate == name), None)

    def blocking(self) -> list[GateName]:
        """Gates that would block approval: missing, or ran-and-failed unwaived."""
        by = {g.gate: g for g in self.gates}
        return [n for n in GATE_NAMES if n not in by or not by[n].clear]

    @property
    def all_clear(self) -> bool:
        return not self.blocking()
