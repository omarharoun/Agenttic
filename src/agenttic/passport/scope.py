"""SPEC-14 Step 66 (M50) — the behavioral scope statement.

This is the half nobody else can produce: a machine-readable statement of *what
was verified* about an agent, generated DIRECTLY from a scorecard, that also names
*the edge of the evidence*. The scope is a fence, not a badge (Hard Rule 65):

* ``verified_capabilities`` — criteria the agent passed on every scored case AND
  whose verdict is trustworthy (a deterministic check, or a judge with a stored
  calibration record). **A provisional (uncalibrated) criterion is NEVER here**
  (Hard Rule 68); it goes to ``provisional_capabilities`` as claimed-but-unproven.
* ``coverage`` / ``coverage_holes`` — closure per coverpoint and the explicit list
  of bins never exercised. A scope that omits the untested bins is a forgery.
* ``not_measured`` — coverpoints the model could not read, with reasons.
* ``assertions`` — properties checked, violations, and unexercised counts.
* ``reliability`` — pass^1 and pass^k with k stated.
* ``envelope`` — cost and latency.
* ``suite_provenance`` — suite/rubric ids + versions, integrity-gate result,
  contamination status.

Every field is derived from the scorecard (Hard Rule 9: no hand-authored numbers).
Calibration state reuses the ONE fail-closed derivation the report uses
(:func:`agenttic.reporting.scorecard_report.criterion_status`), so the passport
and the report can never disagree about what is calibrated.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from agenttic.metrics.reliability import pass_at_1, pass_hat_k
from agenttic.reporting.scorecard_report import (
    CalibrationRecord,
    _default_calibration_records,
    criterion_status,
)


class ScopeIncompleteError(ValueError):
    """A behavioral scope is missing a required section. A scope that does not
    state coverage, reliability, envelope and provenance is not a scope (Hard
    Rule 64/65) — it must not be rendered or signed."""


class VerifiedCapability(BaseModel):
    criterion_id: str
    scorer: str                 # code | judge | fi
    calibration: str            # deterministic | ... calibrated α=.. | ... PROVISIONAL ..
    mean_score: float
    n: int                      # scored cases behind this figure


class BehavioralScope(BaseModel):
    """What an agent was verified to do, and the edge of that evidence."""

    agent_id: str
    agent_config_hash: str
    scorecard_id: str
    suite_id: str
    verified_capabilities: list[VerifiedCapability] = Field(default_factory=list)
    provisional_capabilities: list[VerifiedCapability] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)
    coverage_holes: list[str] = Field(default_factory=list)
    not_measured: list[dict] = Field(default_factory=list)
    assertions: dict = Field(default_factory=dict)
    reliability: dict = Field(default_factory=dict)
    envelope: dict = Field(default_factory=dict)
    suite_provenance: dict = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    #: Structural sections a scope must state to be a fence rather than a badge.
    _REQUIRED = ("coverage", "reliability", "envelope", "suite_provenance")

    def require_complete(self) -> "BehavioralScope":
        """Raise :class:`ScopeIncompleteError` if a required section is empty, or
        if a provisional criterion leaked into ``verified_capabilities``. The check
        a caller runs before rendering or signing the scope."""
        empty = [s for s in self._REQUIRED if not getattr(self, s)]
        if empty:
            raise ScopeIncompleteError(
                f"behavioral scope is missing required section(s): {empty} — a "
                "scope that omits coverage/reliability/envelope/provenance is not "
                "a scope (Hard Rule 64).")
        leaked = [c.criterion_id for c in self.verified_capabilities
                  if "PROVISIONAL" in c.calibration]
        if leaked:
            raise ScopeIncompleteError(
                f"provisional criteria listed as verified: {leaked} "
                "(Hard Rule 68) — provisional is claimed-but-unproven, never verified.")
        return self

    @classmethod
    def from_scorecard(cls, sc, rubric, *,
                       records: dict[str, CalibrationRecord] | None = None,
                       integrity_gate: dict | None = None,
                       contamination: str = "unverified") -> "BehavioralScope":
        """Build a scope from a scorecard + its rubric. No hand-authored fields:
        every value is read off the scorecard, the rubric, or the calibration
        store."""
        records = records if records is not None else _default_calibration_records()
        scorer_by_cid = {s.criterion_id: s.scorer
                         for r in sc.run_scores for s in r.criterion_scores}
        n_by_cid: dict[str, int] = {}
        for r in sc.run_scores:
            if r.scoring_error:
                continue
            for s in r.criterion_scores:
                n_by_cid[s.criterion_id] = n_by_cid.get(s.criterion_id, 0) + 1

        verified: list[VerifiedCapability] = []
        provisional: list[VerifiedCapability] = []
        for cid, mean in sc.per_criterion_means.items():
            scorer = scorer_by_cid.get(cid, "judge")
            cap = VerifiedCapability(
                criterion_id=cid, scorer=scorer,
                calibration=criterion_status(scorer, cid, records),
                mean_score=round(mean, 4), n=n_by_cid.get(cid, 0))
            proven = scorer == "code" or cid in records
            if not proven:
                # uncalibrated judge/fi: claimed-but-unproven, regardless of score.
                provisional.append(cap)
            elif mean >= 1.0:
                # passed on every scored case AND trustworthy verdict.
                verified.append(cap)
            # proven but mean < 1.0: a measured deficiency — not a verified
            # capability, and not provisional. Deliberately not claimed.

        cov = getattr(sc, "coverage", None) or {}
        per_cp = cov.get("per_coverpoint") or {}
        coverage = {
            "model_ref": cov.get("model_ref"),
            "trace_closure": cov.get("trace_closure"),
            "closure_target": cov.get("closure_target"),
            "closed": cov.get("closed"),
            "per_coverpoint": {cp: c.get("closure") for cp, c in per_cp.items()},
        }
        # Every unexercised bin travels with the credential — as a hole (a bin the
        # suite could have hit but didn't) or, for an unreadable coverpoint, in
        # not_measured. Nothing is silently dropped (Hard Rule 65).
        coverage_holes: list[str] = []
        not_measured: list[dict] = []
        for cp, c in per_cp.items():
            if c.get("not_measurable"):
                not_measured.append({
                    "coverpoint": cp,
                    "reason": c.get("not_measurable_reason", "")})
            for b in c.get("unhit", []):
                coverage_holes.append(f"{cp}:{b}")

        # reliability: group runs by case to compute pass^1 and pass^k honestly.
        by_case: dict[str, list[bool]] = {}
        for r in sc.run_scores:
            if r.scoring_error:
                continue
            by_case.setdefault(r.test_id, []).append(bool(r.passed))
        groups = [v for v in by_case.values() if v]
        k = min((len(v) for v in groups), default=0)
        reliability = {
            "pass_1": round(pass_at_1(groups), 4) if groups else None,
            "pass_k": round(pass_hat_k(groups), 4) if groups else None,
            "k": k,
        }

        envelope = {
            "mean_cost_usd": sc.mean_cost_usd,
            "total_cost_usd": sc.total_cost_usd,
            "total_scoring_cost_usd": sc.total_scoring_cost_usd,
            "p95_latency_ms": sc.p95_latency_ms,
        }

        suite_provenance = {
            "suite_id": sc.suite_id,
            "suite_version": sc.suite_version,
            "rubric_id": sc.rubric_id,
            "rubric_version": sc.rubric_version,
            "integrity_gate": integrity_gate or {"status": "not_evaluated"},
            "contamination": contamination,
        }

        return cls(
            agent_id=sc.agent_id,
            agent_config_hash=getattr(sc, "agent_config_hash", "") or "",
            scorecard_id=sc.scorecard_id,
            suite_id=sc.suite_id,
            verified_capabilities=verified,
            provisional_capabilities=provisional,
            coverage=coverage,
            coverage_holes=coverage_holes,
            not_measured=not_measured,
            assertions=dict(cov.get("assertions") or {}),
            reliability=reliability,
            envelope=envelope,
            suite_provenance=suite_provenance,
        )
