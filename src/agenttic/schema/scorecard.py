"""Scorecard schema — the output contract of the scoring engine (UVM: scoreboard report)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from agenttic.verification.assertions import AssertionResult


class CriterionScore(BaseModel):
    """Score for one criterion on one run."""

    criterion_id: str
    score: float  # binary: {0,1}; three_point: {0, 0.5, 1}
    scorer: Literal["code", "judge", "fi"]
    calibrated: bool = True  # Hard Rule 6: False => shown as PROVISIONAL
    judge_rationale: str | None = None
    cost_usd: float = 0.0  # scoring cost for this criterion (judge tokens; 0 for code)

    @model_validator(mode="after")
    def _score_in_scale(self) -> "CriterionScore":
        if self.score not in (0.0, 0.5, 1.0):
            raise ValueError(
                f"criterion {self.criterion_id}: score {self.score} outside "
                "allowed values {0, 0.5, 1} (Hard Rule 3)"
            )
        return self


class RunScore(BaseModel):
    """All criterion scores for one trace against one test case.

    ``scoring_error`` is set when the case could not be scored (judge/FI
    outage, missing check config, ...). Such a run is kept and surfaced but
    excluded from quality aggregates — a scoring-infra failure is not an
    agent task failure (partial batch scoring)."""

    trace_id: str
    test_id: str
    criterion_scores: list[CriterionScore]
    passed: bool
    cost_usd: float = 0.0           # agent execution cost (from the trace)
    scoring_cost_usd: float = 0.0   # judge/scoring cost for this run
    latency_ms: float = 0.0
    steps: int = 0
    scoring_error: str | None = None


class Scorecard(BaseModel):
    """Aggregated result of an agent against a versioned suite."""

    scorecard_id: str
    agent_id: str
    suite_id: str
    suite_version: int
    rubric_id: str
    rubric_version: int
    run_scores: list[RunScore]
    task_success_rate: float
    mean_cost_usd: float            # mean agent execution cost per run
    total_cost_usd: float = 0.0     # total agent execution cost over all runs
    total_scoring_cost_usd: float = 0.0  # total judge/scoring cost over all runs
    p95_latency_ms: float
    per_criterion_means: dict[str, float] = Field(default_factory=dict)
    errored_test_ids: list[str] = Field(default_factory=list)
    visibility_tier: Literal["glass_box", "black_box"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- sample size + confidence (additive; derived from run_scores) --------
    # task_success_rate is a point estimate; on its own it hides how much data
    # backs it. These computed fields travel with every serialized scorecard so
    # the UI can show n and a Wilson 95% interval next to the headline rate
    # (never a bare % again). Derived, so old stored scorecards gain them for
    # free on read.

    @computed_field  # type: ignore[prop-decorator]
    @property
    def n_scored(self) -> int:
        """Cases that were actually scored (scoring errors excluded) — the n the
        pass-rate is computed over."""
        return sum(1 for r in self.run_scores if r.scoring_error is None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def n_passed(self) -> int:
        return sum(1 for r in self.run_scores
                   if r.scoring_error is None and r.passed)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success_wilson_low(self) -> float:
        """Wilson 95% lower bound on the pass-rate — the defensible floor."""
        from agenttic.stats import wilson_interval
        return round(wilson_interval(self.n_passed, self.n_scored)[0], 4)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success_wilson_high(self) -> float:
        from agenttic.stats import wilson_interval
        return round(wilson_interval(self.n_passed, self.n_scored)[1], 4)

    # --- assertions (SPEC-13 Step 62) ----------------------------------------
    # Continuous properties monitored on every trace. Deliberately a SEPARATE
    # block: assertion results never enter criterion scores, the weighted mean,
    # or task_success_rate — the scoring engine's behaviour is unchanged. A
    # violation is surfaced through ``verification_status`` instead (Hard Rule 59).
    assertions: list["AssertionResult"] = Field(default_factory=list)
    assertion_set_ref: str = ""      # the pinned set that was in force

    @computed_field  # type: ignore[prop-decorator]
    @property
    def assertion_violations(self) -> int:
        return sum(1 for a in self.assertions if a.status == "violation")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def assertions_unexercised(self) -> int:
        """Vacuity: properties that never had their antecedent occur. NOT passes
        (Hard Rule 60)."""
        return sum(1 for a in self.assertions if a.status == "unexercised")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verification_status(self) -> str:
        """FAIL if any assertion violated, regardless of criteria scores."""
        return "FAIL" if self.assertion_violations else "PASS"

    def violated_properties(self) -> list[str]:
        """The named properties that failed — what the report prints."""
        return [a.detail for a in self.assertions if a.status == "violation"]

    @classmethod
    def aggregate(
        cls,
        *,
        scorecard_id: str,
        agent_id: str,
        suite_id: str,
        suite_version: int,
        rubric_id: str,
        rubric_version: int,
        run_scores: list[RunScore],
        visibility_tier: Literal["glass_box", "black_box"],
    ) -> "Scorecard":
        """Compute aggregates from run scores (single place, so reports never recompute).

        Quality metrics (task_success_rate, per_criterion_means) are computed
        over the *scored* subset — runs whose ``scoring_error`` is set are kept
        in ``run_scores`` and listed in ``errored_test_ids`` but excluded, so a
        judge/FI outage can't masquerade as the agent failing the task.
        Execution metrics (mean_cost, p95_latency) cover *all* runs: the agent
        ran and incurred cost regardless of whether scoring later succeeded."""
        n = len(run_scores)
        if n == 0:
            raise ValueError("cannot aggregate an empty run set")
        scored = [r for r in run_scores if r.scoring_error is None]
        errored_ids = [r.test_id for r in run_scores if r.scoring_error is not None]
        s = len(scored)
        success = (sum(1 for r in scored if r.passed) / s) if s else 0.0
        total_cost = sum(r.cost_usd for r in run_scores)
        total_scoring_cost = sum(r.scoring_cost_usd for r in run_scores)
        mean_cost = total_cost / n
        latencies = sorted(r.latency_ms for r in run_scores)
        p95 = latencies[min(n - 1, max(0, round(0.95 * n) - 1))]
        per_crit: dict[str, list[float]] = {}
        for r in scored:
            for c in r.criterion_scores:
                per_crit.setdefault(c.criterion_id, []).append(c.score)
        means = {cid: sum(v) / len(v) for cid, v in per_crit.items()}
        return cls(
            scorecard_id=scorecard_id,
            agent_id=agent_id,
            suite_id=suite_id,
            suite_version=suite_version,
            rubric_id=rubric_id,
            rubric_version=rubric_version,
            run_scores=run_scores,
            task_success_rate=success,
            mean_cost_usd=mean_cost,
            total_cost_usd=total_cost,
            total_scoring_cost_usd=total_scoring_cost,
            p95_latency_ms=p95,
            per_criterion_means=means,
            errored_test_ids=errored_ids,
            visibility_tier=visibility_tier,
        )
