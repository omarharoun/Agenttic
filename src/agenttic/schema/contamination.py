"""Contamination report schema (SPEC-6 Step 28).

Public benchmarks are structurally contaminated — public repos get trained on.
Client suites are private by construction; this makes that a verifiable claim.
For a given (agent, suite) we probe two ways: does the agent regurgitate the
suite's per-tenant canary, and does it ace only the exact stored cases while
failing perturbed variants of the same difficulty (memorisation, not skill).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ContaminationReport(BaseModel):
    suite_id: str
    version: int
    agent_id: str
    origin: Literal["private", "imported"]
    #: the agent reproduced the suite's private canary verbatim
    canary_regurgitated: bool
    #: fraction of perturbable cases the agent passed as-written but failed when
    #: perturbed (a memorisation signal); None when nothing was perturbable
    perturbation_gap: float | None
    n_perturbed: int
    #: plausibly exposed to the suite (canary regurgitation or a high gap)
    exposed: bool
    detail: str

    def report_line(self) -> str:
        """The one standard line for the client report."""
        canary = "regurgitated" if self.canary_regurgitated else "intact"
        gap = "n/a" if self.perturbation_gap is None else f"{self.perturbation_gap:.0%}"
        exposure = "PLAUSIBLE — investigate" if self.exposed else "none detected"
        return (f"Suite origin: {self.origin} · canary: {canary} · "
                f"perturbation gap: {gap} · agent exposure: {exposure}")
