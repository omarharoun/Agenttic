"""Agenttic Index leaderboard — ranks agents across suites (artificialanalysis.ai
style). Reads scorecard summaries; per-suite weights come from config."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ascore.leaderboard import compute_leaderboard

router = APIRouter(tags=["leaderboard"])


@router.get("/leaderboard")
def leaderboard(request: Request,
                suites: str = Query("", description="comma-separated suite_ids "
                                    "to restrict to a common set")):
    state = request.state
    weights = (state.cfg.get("leaderboard", {}) or {}).get("suite_weights", {})
    suite_filter = [s for s in suites.split(",") if s] or None
    declared_types = {a["agent_id"]: a["variant"]
                      for a in state.reg.list_declared_agents(include_retired=True)}
    board = compute_leaderboard(
        state.store.list_scorecards(), weights=weights, suite_filter=suite_filter,
        declared_types=declared_types)
    board["weights"] = weights
    return board
