"""Scorecard schema — the output contract of the scoring engine (UVM: scoreboard report)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CriterionScore(BaseModel):
    """Score for one criterion on one run."""

    criterion_id: str
    score: float  # binary: {0,1}; three_point: {0, 0.5, 1}
    scorer: Literal["code", "judge"]
    calibrated: bool = True  # Hard Rule 6: False => shown as PROVISIONAL
    judge_rationale: str | None = None

    @model_validator(mode="after")
    def _score_in_scale(self) -> "CriterionScore":
        if self.score not in (0.0, 0.5, 1.0):
            raise ValueError(
                f"criterion {self.criterion_id}: score {self.score} outside "
                "allowed values {0, 0.5, 1} (Hard Rule 3)"
            )
        return self


class RunScore(BaseModel):
    """All criterion scores for one trace against one test case."""

    trace_id: str
    test_id: str
    criterion_scores: list[CriterionScore]
    passed: bool
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    steps: int = 0


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
    mean_cost_usd: float
    p95_latency_ms: float
    per_criterion_means: dict[str, float] = Field(default_factory=dict)
    visibility_tier: Literal["glass_box", "black_box"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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
        """Compute aggregates from run scores (single place, so reports never recompute)."""
        n = len(run_scores)
        if n == 0:
            raise ValueError("cannot aggregate an empty run set")
        success = sum(1 for r in run_scores if r.passed) / n
        mean_cost = sum(r.cost_usd for r in run_scores) / n
        latencies = sorted(r.latency_ms for r in run_scores)
        p95 = latencies[min(n - 1, max(0, round(0.95 * n) - 1))]
        per_crit: dict[str, list[float]] = {}
        for r in run_scores:
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
            p95_latency_ms=p95,
            per_criterion_means=means,
            visibility_tier=visibility_tier,
        )
