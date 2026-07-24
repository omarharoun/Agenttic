"""The verification sign-off — what replaces the pass rate (SPEC-13 Step 64).

The deliverable stops being *"your agent scored 86%"* and becomes what a chip
gets before tape-out: **coverage model closed, assertions clean across N
generated scenarios, safety properties discharged over the authorization layer,
bug curve flat.**

Six legs plus provenance, every one of which can say "not run" rather than
quietly reading as success:

* **coverage** — closure per coverpoint and cross, unhit bins, waivers with
  reasons, `other`-bin drift
* **assertions** — total, violations, unexercised (vacuous)
* **formal** — properties proven / counterexampled / unbounded / not attempted,
  each with its scope
* **convergence** — the bug-discovery curve and scenarios since the last new
  failure signature
* **regression** — pass^k on the directed suite of frozen historical bugs
* **envelope** — cost and latency
* **provenance** — the calibration state of every judge and classifier used

A pass rate is still reported, but **demoted to one line**, and when no coverage
model was present it renders `unscoped — no coverage model` (Hard Rule 56).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from agenttic.schema.attestation import content_hash

LegStatus = Literal["populated", "not_run"]


class CoverageLeg(BaseModel):
    status: LegStatus = "not_run"
    model_ref: str = ""
    bins_fingerprint: str = ""
    trace_closure: float = 0.0
    stimulus_closure: float = 0.0
    closure_target: float = 0.95
    closed: bool = False
    unhit_bins: list[str] = Field(default_factory=list)
    waived_bins: dict[str, str] = Field(default_factory=dict)   # bin -> reason
    other_drift: dict[str, float] = Field(default_factory=dict)
    illegal_hits: list[str] = Field(default_factory=list)
    provisional_coverpoints: list[str] = Field(default_factory=list)


class AssertionLeg(BaseModel):
    status: LegStatus = "not_run"
    assertion_set_ref: str = ""
    total: int = 0
    violations: int = 0
    unexercised: int = 0
    exercised_ratio: float = 0.0
    violated_properties: list[str] = Field(default_factory=list)
    unexercised_properties: list[str] = Field(default_factory=list)

    @property
    def verdict(self) -> str:
        return "FAIL" if self.violations else "PASS"


class FormalLeg(BaseModel):
    status: LegStatus = "not_run"
    proven: int = 0
    counterexample: int = 0
    unbounded: int = 0
    not_attempted: int = 0
    scope: str = "the tool-authorization layer"
    claims: list[str] = Field(default_factory=list)


class ConvergenceLeg(BaseModel):
    status: LegStatus = "not_run"
    scenarios_run: int = 0
    distinct_failure_signatures: int = 0
    scenarios_since_last_new_signature: int = 0
    curve_flattened: bool = False
    bug_curve: list[tuple[int, int]] = Field(default_factory=list)


class RegressionLeg(BaseModel):
    status: LegStatus = "not_run"
    frozen_cases: int = 0
    k: int = 1
    pass_hat_k: float = 0.0


class EnvelopeLeg(BaseModel):
    status: LegStatus = "not_run"
    mean_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    p95_latency_ms: float = 0.0
    closure_per_dollar: float = 0.0


class ProvenanceLeg(BaseModel):
    """The calibration state of everything that made a judgement. An
    uncalibrated judge inside a coverage model is exactly the false confidence
    this platform exists to prevent, so it is named here."""

    status: LegStatus = "not_run"
    judges: dict[str, str] = Field(default_factory=dict)        # id -> state
    classifiers: dict[str, str] = Field(default_factory=dict)   # id -> state
    harness_version: str = ""

    @property
    def any_provisional(self) -> bool:
        return any(v != "calibrated"
                   for v in {**self.judges, **self.classifiers}.values())


class VerificationSignoff(BaseModel):
    """The headline of every report and certificate."""

    signoff_id: str
    agent_id: str
    agent_config_hash: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    coverage: CoverageLeg = Field(default_factory=CoverageLeg)
    assertions: AssertionLeg = Field(default_factory=AssertionLeg)
    formal: FormalLeg = Field(default_factory=FormalLeg)
    convergence: ConvergenceLeg = Field(default_factory=ConvergenceLeg)
    regression: RegressionLeg = Field(default_factory=RegressionLeg)
    envelope: EnvelopeLeg = Field(default_factory=EnvelopeLeg)
    provenance: ProvenanceLeg = Field(default_factory=ProvenanceLeg)

    #: demoted to one line among several
    pass_rate: float | None = None

    #: the six legs the acceptance criteria require to be populated
    LEGS: ClassVar[tuple[str, ...]] = (
        "coverage", "assertions", "formal", "convergence", "regression",
        "envelope")

    @property
    def unscoped(self) -> bool:
        """A pass rate with no coverage model is an unscoped claim (HR56)."""
        return self.coverage.status != "populated"

    @property
    def pass_rate_label(self) -> str:
        if self.pass_rate is None:
            return "not measured"
        if self.unscoped:
            return f"{self.pass_rate:.0%} — unscoped (no coverage model)"
        return f"{self.pass_rate:.0%}"

    def populated_legs(self) -> list[str]:
        return [n for n in self.LEGS if getattr(self, n).status == "populated"]

    def missing_legs(self) -> list[str]:
        return [n for n in self.LEGS if getattr(self, n).status != "populated"]

    @property
    def complete(self) -> bool:
        return not self.missing_legs()

    @property
    def signs_off(self) -> bool:
        """The sign-off verdict: closure met, no assertion violations, no formal
        counterexample, and no illegal-bin hit. Deny-by-default — a leg that did
        not run cannot contribute a pass."""
        return (self.coverage.status == "populated" and self.coverage.closed
                and self.assertions.status == "populated"
                and self.assertions.violations == 0
                and self.formal.counterexample == 0
                and not self.coverage.illegal_hits)

    def content_sha256(self) -> str:
        data = self.model_dump(mode="json")
        data.pop("created_at", None)
        return content_hash(data)


def build_signoff(
    *, signoff_id: str, agent_id: str, agent_config_hash: str = "",
    coverage_report=None, assertion_results=None, proof_results=None,
    cdv_result=None, regression=None, scorecard=None, provenance=None,
) -> VerificationSignoff:
    """Assemble a sign-off from the real artifacts. Any leg whose artifact is
    absent stays ``not_run`` — it never silently reads as a pass."""
    s = VerificationSignoff(signoff_id=signoff_id, agent_id=agent_id,
                            agent_config_hash=agent_config_hash)

    if coverage_report is not None:
        cr = coverage_report
        s.coverage = CoverageLeg(
            status="populated", model_ref=cr.model_ref,
            bins_fingerprint=cr.bins_fingerprint,
            trace_closure=cr.trace_closure, stimulus_closure=cr.stimulus_closure,
            closure_target=cr.closure_target, closed=cr.closed,
            unhit_bins=[f"{cp.coverpoint_id}.{b}" for cp in cr.coverpoints.values()
                        for b in cp.unhit],
            other_drift=cr.other_drift(),
            illegal_hits=[f"{i.coverpoint_id}.{i.bin_id}" for i in cr.illegal_hits],
            provisional_coverpoints=cr.provisional_coverpoints)

    if assertion_results is not None:
        from agenttic.verification.assertions import summarize
        summ = summarize(assertion_results)
        s.assertions = AssertionLeg(
            status="populated", total=summ["total"],
            violations=summ["violations"], unexercised=summ["unexercised"],
            exercised_ratio=summ["exercised_ratio"],
            violated_properties=[v["detail"] for v in summ["violated_properties"]],
            unexercised_properties=summ["unexercised_properties"])

    if proof_results is not None:
        counts = {k: sum(1 for r in proof_results if r.status == k)
                  for k in ("proven", "counterexample", "unbounded", "not_attempted")}
        s.formal = FormalLeg(status="populated", **counts,
                             claims=[r.claim() for r in proof_results])

    if cdv_result is not None:
        s.convergence = ConvergenceLeg(
            status="populated", scenarios_run=cdv_result.scenarios_run,
            distinct_failure_signatures=cdv_result.distinct_signatures,
            scenarios_since_last_new_signature=(
                cdv_result.scenarios_since_last_new_signature()),
            curve_flattened=cdv_result.curve_flattened(),
            bug_curve=list(cdv_result.bug_curve))
        s.envelope = EnvelopeLeg(
            status="populated", total_cost_usd=cdv_result.dollars_spent,
            mean_cost_usd=(cdv_result.dollars_spent / cdv_result.scenarios_run
                           if cdv_result.scenarios_run else 0.0),
            closure_per_dollar=cdv_result.closure_per_dollar)

    if regression is not None:
        s.regression = RegressionLeg(status="populated", **regression)

    if scorecard is not None:
        s.pass_rate = getattr(scorecard, "task_success_rate", None)
        if getattr(scorecard, "p95_latency_ms", None) is not None:
            s.envelope.p95_latency_ms = scorecard.p95_latency_ms
            s.envelope.status = "populated"

    if provenance is not None:
        s.provenance = ProvenanceLeg(status="populated", **provenance)
    return s
