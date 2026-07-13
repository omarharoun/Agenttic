"""Prompt-optimization schema — the artifact of a self-improving system-prompt
loop (see :mod:`agenttic.optimizer`).

The optimizer keeps the **model frozen** and treats the suite's pass rate as the
reward, iteratively editing the agent's SYSTEM PROMPT to fix the criteria it
fails. The result is a *prompt lineage*: a baseline prompt and a chain of
candidate edits, each with the scorecard it earned on the **train** split and
(when accepted) on a held-out split the optimizer never sees.

Why a held-out split is part of the artifact: an optimizer climbing a narrow,
noisy reward can get genuinely worse while the train number goes up. Reporting
``train`` vs ``heldout`` for the baseline and the best prompt makes that
overfitting visible — ``overfit_gap`` quantifies it (how much more the prompt
improved on the cases it was tuned against than on cases it never saw).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class PerCriterionRegression(BaseModel):
    """A criterion a candidate significantly *worsened* vs the current best —
    the regression-protection signal that vetoes an otherwise-improving edit."""
    criterion_id: str
    delta: float                 # mean_b - mean_a (<0: candidate scores lower)
    p_value: float
    n: int


class CandidateResult(BaseModel):
    """One proposed prompt evaluated against the current best on the train split.

    The decision uses the same paired stats the A/B engine produces
    (:func:`agenttic.ab.compare_scorecards`): ``success_delta`` is the net pass-rate
    change, and ``regressions`` lists any criterion the edit significantly broke."""
    index: int                   # candidate index within its round
    system_prompt: str
    rationale: str = ""          # why the optimizer proposed this edit
    scorecard_id: str = ""       # the candidate's train scorecard
    comparison_id: str = ""      # paired comparison vs the round's baseline
    train_success_rate: float = 0.0
    success_delta: float = 0.0   # rate(candidate) - rate(baseline) on train
    mcnemar_p: float | None = None
    regressions: list[PerCriterionRegression] = Field(default_factory=list)
    accepted: bool = False
    reason: str = ""             # human-readable accept/reject reason


class OptimizationRound(BaseModel):
    """One reflect→propose→evaluate cycle. ``baseline_version`` is the best
    prompt going in; ``chosen_index`` is the candidate adopted (None: round made
    no improvement, best prompt unchanged)."""
    round: int
    baseline_version: int
    baseline_train_rate: float
    failing_criteria: list[str] = Field(default_factory=list)  # what it targeted
    candidates: list[CandidateResult] = Field(default_factory=list)
    chosen_index: int | None = None


class PromptVersion(BaseModel):
    """A node in the prompt lineage: a system prompt + the scores it earned.

    ``parent_version`` chains edits back to the baseline (version 0). Every
    accepted version is scored on BOTH splits; ``heldout_success_rate`` is None
    for the baseline only if held-out scoring was skipped."""
    version: int
    system_prompt: str
    parent_version: int | None = None
    rationale: str = ""
    train_success_rate: float | None = None
    heldout_success_rate: float | None = None
    train_scorecard_id: str = ""
    heldout_scorecard_id: str = ""
    accepted: bool = True        # baseline + adopted candidates are "accepted"


class OptimizationRun(BaseModel):
    """The full record of one optimization: baseline → best prompt, the lineage,
    every round's accept/reject reasoning, and the train-vs-heldout numbers that
    make overfitting detectable. Persisted append-only in the registry."""

    run_id: str
    agent_id: str
    suite_id: str
    suite_version: int
    methodology: str = "OPRO/ProTeGi reflective prompt optimization"

    status: Literal["running", "succeeded", "failed"] = "running"
    error: str = ""

    # configuration (bounded + cost-aware)
    rounds_requested: int = 0
    candidates_per_round: int = 0
    heldout_fraction: float = 0.0
    seed: int = 0

    # the split (recorded so the run is reproducible / auditable)
    n_train: int = 0
    n_heldout: int = 0
    train_test_ids: list[str] = Field(default_factory=list)
    heldout_test_ids: list[str] = Field(default_factory=list)

    # prompts + lineage
    baseline_prompt: str = ""
    best_prompt: str = ""
    best_version: int = 0
    lineage: list[PromptVersion] = Field(default_factory=list)
    rounds: list[OptimizationRound] = Field(default_factory=list)

    # headline scores (the overfitting guard's numbers)
    baseline_train_rate: float = 0.0
    best_train_rate: float = 0.0
    baseline_heldout_rate: float | None = None
    best_heldout_rate: float | None = None

    total_cost_usd: float = 0.0
    n_agent_runs: int = 0        # suite executions performed (the cost driver)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def train_gain(self) -> float:
        return self.best_train_rate - self.baseline_train_rate

    @property
    def heldout_gain(self) -> float | None:
        if self.baseline_heldout_rate is None or self.best_heldout_rate is None:
            return None
        return self.best_heldout_rate - self.baseline_heldout_rate

    @property
    def overfit_gap(self) -> float | None:
        """How much more the prompt improved on the cases it was tuned against
        than on cases it never saw. A large positive gap = the gains are likely
        memorized to the suite, not real generalization."""
        hg = self.heldout_gain
        if hg is None:
            return None
        return self.train_gain - hg

    @property
    def improved(self) -> bool:
        return self.best_version > 0 and self.best_train_rate > self.baseline_train_rate
