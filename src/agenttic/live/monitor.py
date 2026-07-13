"""Live monitoring (Step 9) — the production-traffic path.

Production traces (no test_case_id) are ingested, sampled at a configured
rate, and scored on the reduced rubric (criteria tagged ``live``) with the
lightweight judge. Rolling per-criterion means are compared to the agent's
batch baseline; a drop beyond the drift threshold emits a ReEvalRequest.

The live path's job is DRIFT DETECTION, not precise scoring. Live scores are
stored in their own tables and never mix into batch scorecards.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from agenttic.registry.sqlite_store import Registry
from agenttic.schema.rubric import Criterion
from agenttic.schema.scorecard import Scorecard
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import Trace
from agenttic.scoring.judge import LLMJudge

_LIVE_TC = TestCase(
    test_id="live-traffic", suite_id="live", version=1,
    task_description="production traffic (no scripted test case)",
    input={}, rubric_id="live",
)


@dataclass
class DriftStatus:
    agent_id: str
    window: int
    per_criterion_mean: dict[str, float]
    baseline_mean: dict[str, float]
    drifted: list[str] = field(default_factory=list)

    @property
    def drift_detected(self) -> bool:
        return bool(self.drifted)


class LiveMonitor:
    def __init__(
        self,
        *,
        registry: Registry,
        judge: LLMJudge,
        live_criteria: list[Criterion],
        sample_rate: float = 0.05,
        drift_threshold: float = 0.15,
        window: int = 50,
        rng: random.Random | None = None,
    ):
        bad = [c.criterion_id for c in live_criteria
               if c.scorer != "judge" or "live" not in c.tags]
        if bad:
            raise ValueError(
                f"live criteria must be judge-scored and tagged 'live': {bad}"
            )
        self.registry = registry
        self.judge = judge
        self.live_criteria = live_criteria
        self.sample_rate = sample_rate
        self.drift_threshold = drift_threshold
        self.window = window
        self.rng = rng or random.Random()

    def ingest(self, trace: Trace) -> bool:
        """Store a production trace; sample-score it. Returns True if scored."""
        if trace.test_case_id is not None:
            raise ValueError(
                f"trace {trace.trace_id} has a test_case_id — that's a batch "
                "trace; the live path only ingests production traffic"
            )
        self.registry.save_trace(trace, mode="live")
        if self.rng.random() >= self.sample_rate:
            return False
        scores = {
            c.criterion_id: self.judge.score_criterion(c, trace, _LIVE_TC).score
            for c in self.live_criteria
        }
        self.registry.save_live_scores(trace.agent_id, trace.trace_id, scores)
        return True

    def status(self, agent_id: str, baseline: Scorecard) -> DriftStatus:
        """Compare rolling live means against the batch baseline; record a
        ReEvalRequest for every drifted criterion."""
        means: dict[str, float] = {}
        drifted: list[str] = []
        base = baseline.per_criterion_means
        for c in self.live_criteria:
            cid = c.criterion_id
            window_scores = self.registry.live_scores(agent_id, cid, self.window)
            if not window_scores or cid not in base:
                continue
            means[cid] = sum(window_scores) / len(window_scores)
            if base[cid] - means[cid] > self.drift_threshold:
                drifted.append(cid)
        status = DriftStatus(agent_id=agent_id, window=self.window,
                             per_criterion_mean=means,
                             baseline_mean={k: base[k] for k in means},
                             drifted=drifted)
        for cid in drifted:
            self.registry.save_reeval_request(
                agent_id,
                f"criterion {cid!r} live mean {means[cid]:.2f} dropped more than "
                f"{self.drift_threshold} below batch baseline {base[cid]:.2f} "
                f"(window={self.window}) — batch re-evaluation recommended",
            )
        return status
