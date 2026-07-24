"""The verification plan (vPlan) — traceability (SPEC-13 Step 64).

requirement → coverpoint(s) → assertion(s) → criteria → results.

**Requirements with no mapped coverpoint or assertion are flagged UNTESTED,
loudly.** That single line is the product: no eval tool on the market can tell
you which of your requirements nothing is testing, because none of them has a
declared model of what "tested" means to trace against.

A requirement mapped to a coverpoint whose bins were never hit is *mapped but
unexercised* — distinct from untested, and reported separately, because the two
have different fixes (write a test vs. run more stimulus).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from pydantic import BaseModel, Field

TraceStatus = Literal["covered", "unexercised", "untested"]


class Requirement(BaseModel):
    """One thing the agent is required to do, and what tests it."""

    requirement_id: str
    text: str
    coverpoints: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)
    criteria: list[str] = Field(default_factory=list)
    #: a requirement may be deliberately out of scope, with a reason
    waived: bool = False
    reason: str = ""

    @property
    def mapped(self) -> bool:
        return bool(self.coverpoints or self.assertions or self.criteria)


class VPlan(BaseModel):
    plan_id: str
    version: int = 1
    requirements: list[Requirement] = Field(default_factory=list)

    def ref(self) -> str:
        return f"vplan:{self.plan_id}@v{self.version}"


@dataclass
class TraceRow:
    requirement_id: str
    text: str
    status: TraceStatus
    coverpoints: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def untested(self) -> bool:
        return self.status == "untested"


@dataclass
class VPlanTrace:
    plan_ref: str
    rows: list[TraceRow] = field(default_factory=list)

    @property
    def untested(self) -> list[TraceRow]:
        return [r for r in self.rows if r.status == "untested"]

    @property
    def unexercised(self) -> list[TraceRow]:
        return [r for r in self.rows if r.status == "unexercised"]

    @property
    def covered(self) -> list[TraceRow]:
        return [r for r in self.rows if r.status == "covered"]

    @property
    def coverage_of_requirements(self) -> float:
        scored = [r for r in self.rows]
        return (len(self.covered) / len(scored)) if scored else 0.0

    def as_dict(self) -> dict:
        return {
            "plan": self.plan_ref,
            "requirements": len(self.rows),
            "covered": len(self.covered),
            "unexercised": len(self.unexercised),
            "untested": len(self.untested),
            "untested_requirements": [
                {"requirement_id": r.requirement_id, "text": r.text}
                for r in self.untested],
            "rows": [{"requirement_id": r.requirement_id, "status": r.status,
                      "coverpoints": r.coverpoints, "assertions": r.assertions,
                      "criteria": r.criteria, "detail": r.detail}
                     for r in self.rows],
        }


def trace(vplan: VPlan, *, coverage_report=None, assertion_results=None,
          criteria_scored: Sequence[str] | None = None) -> VPlanTrace:
    """Compute traceability. A requirement is:

    * **untested** — nothing maps to it at all (the loud case), or everything it
      maps to does not exist in the model/registry that ran;
    * **unexercised** — mapped, but the coverpoints it names have unhit bins and
      no assertion of its own was exercised;
    * **covered** — mapped, and something that ran actually exercised it.
    """
    out = VPlanTrace(plan_ref=vplan.ref())
    known_cps = set((coverage_report.coverpoints if coverage_report else {}) or {})
    exercised_assertions = {
        a.assertion_id for a in (assertion_results or [])
        if getattr(a, "status", "") != "unexercised"}
    known_assertions = {a.assertion_id for a in (assertion_results or [])}
    scored = set(criteria_scored or [])

    for req in vplan.requirements:
        row = TraceRow(requirement_id=req.requirement_id, text=req.text,
                       status="untested", coverpoints=list(req.coverpoints),
                       assertions=list(req.assertions), criteria=list(req.criteria))
        if req.waived:
            row.status = "covered"
            row.detail = f"waived: {req.reason}"
            out.rows.append(row)
            continue
        if not req.mapped:
            row.detail = ("NOTHING TESTS THIS — no coverpoint, assertion or "
                          "criterion is mapped to this requirement")
            out.rows.append(row)
            continue

        evidence: list[str] = []
        exercised = False
        mapped_to_something_real = False

        for cp_id in req.coverpoints:
            if cp_id not in known_cps:
                continue
            mapped_to_something_real = True
            cp = coverage_report.coverpoints[cp_id]
            hit = [b for b in cp.countable() if b.hit]
            evidence.append(f"coverpoint {cp_id}: {len(hit)}/{len(cp.countable())} bins hit")
            if hit:
                exercised = True

        for a_id in req.assertions:
            if a_id not in known_assertions:
                continue
            mapped_to_something_real = True
            if a_id in exercised_assertions:
                exercised = True
                evidence.append(f"assertion {a_id}: exercised")
            else:
                evidence.append(f"assertion {a_id}: UNEXERCISED (not evidence)")

        for c_id in req.criteria:
            if c_id in scored:
                mapped_to_something_real = True
                exercised = True
                evidence.append(f"criterion {c_id}: scored")

        row.evidence = evidence
        if not mapped_to_something_real:
            row.detail = ("mapped, but none of its coverpoints/assertions/criteria "
                          "exist in what actually ran — treated as UNTESTED")
            row.status = "untested"
        elif exercised:
            row.status = "covered"
        else:
            row.status = "unexercised"
            row.detail = ("mapped, but nothing it names was actually exercised — "
                          "run more stimulus")
        out.rows.append(row)
    return out
