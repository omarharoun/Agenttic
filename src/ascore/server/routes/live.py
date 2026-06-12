"""Live production path over HTTP (Step 9): ingest traces from deployed
agents, sample-score them with the light judge on the rubric's live-tagged
criteria, and report drift vs a batch-baseline scorecard.

Live data stays in its own tables — nothing here touches batch scorecards.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ascore.live.monitor import LiveMonitor
from ascore.registry.sqlite_store import NotFoundError
from ascore.schema.trace import Trace
from ascore.scoring.judge import LLMJudge

router = APIRouter(tags=["live"])


def _monitor(state, rubric_id: str, agent_id: str) -> LiveMonitor:
    try:
        rubric = state.reg.get_rubric(rubric_id)
    except NotFoundError:
        raise HTTPException(404, f"rubric {rubric_id} not found")
    live_criteria = [c for c in rubric.criteria
                     if c.scorer == "judge" and "live" in c.tags]
    live_cfg = state.cfg.get("live", {})
    judge = LLMJudge(
        model=state.cfg["models"]["judge_light"],
        # production traces don't carry a model string; the placeholder can
        # never collide with a judge tier (same convention as black-box)
        agent_model=f"live:{agent_id}",
        client=state.clients.get("judge"),
    )
    return LiveMonitor(
        registry=state.reg, judge=judge, live_criteria=live_criteria,
        sample_rate=live_cfg.get("sample_rate", 0.05),
        drift_threshold=live_cfg.get("drift_threshold", 0.15),
        window=live_cfg.get("drift_window_runs", 50),
    )


@router.post("/live/ingest")
def ingest(trace: Trace, request: Request, rubric_id: str):
    """Ingest one production trace (no test_case_id). Sync route — FastAPI
    runs it in the threadpool, so the sampled judge call doesn't block the
    event loop."""
    state = request.app.state
    monitor = _monitor(state, rubric_id, trace.agent_id)
    try:
        scored = monitor.ingest(trace)
    except ValueError as exc:  # batch trace sent to the live path
        raise HTTPException(422, str(exc))
    return {"trace_id": trace.trace_id, "stored": True, "scored": scored}


@router.get("/live/{agent_id}/status")
def live_status(agent_id: str, request: Request, rubric_id: str,
                baseline_scorecard_id: str):
    state = request.app.state
    try:
        baseline = state.reg.get_scorecard(baseline_scorecard_id)
    except NotFoundError:
        raise HTTPException(404, f"scorecard {baseline_scorecard_id} not found")
    monitor = _monitor(state, rubric_id, agent_id)
    status = monitor.status(agent_id, baseline)
    return {
        "agent_id": agent_id, "window": status.window,
        "per_criterion_mean": status.per_criterion_mean,
        "baseline_mean": status.baseline_mean,
        "drifted": status.drifted,
        "drift_detected": status.drift_detected,
        "reeval_requests": state.reg.reeval_requests(agent_id),
    }
