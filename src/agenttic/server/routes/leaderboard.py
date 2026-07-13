"""Agenttic Index leaderboard — ranks agents across suites (artificialanalysis.ai
style). Reads scorecard summaries; per-suite weights come from config."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from agenttic.leaderboard import compute_leaderboard

router = APIRouter(tags=["leaderboard"])


@router.get("/leaderboard")
def leaderboard(request: Request,
                suites: str = Query("", description="comma-separated suite_ids "
                                    "to restrict to a common set"),
                certified: bool = Query(False, description="only certified agents")):
    state = request.state
    weights = (state.cfg.get("leaderboard", {}) or {}).get("suite_weights", {})
    suite_filter = [s for s in suites.split(",") if s] or None
    declared_types = {a["agent_id"]: a["variant"]
                      for a in state.reg.list_declared_agents(include_retired=True)}
    board = compute_leaderboard(
        state.store.list_scorecards(), weights=weights, suite_filter=suite_filter,
        declared_types=declared_types)
    # Hard Rule 17: Index-imported (Catalog-only) agents never appear on score
    # leaderboards. They have no scorecards, but exclude by convention too.
    index_agents = {c["agent_id"] for c in state.reg.list_cards(source="index_import")}
    board["agents"] = [r for r in board.get("agents", [])
                       if r["agent_id"] not in index_agents]
    _attach_certification_badges(state.reg, board.get("agents", []))
    if certified:
        board["agents"] = [r for r in board.get("agents", [])
                           if r.get("certification") is not None]
    board["weights"] = weights
    return board


def _attach_certification_badges(reg, rows: list[dict]) -> None:
    """Attach a certification badge (tier + attestation + computed status) to
    each row that has a dossier. Uncertified agents get ``certification: None``
    (the UI shows nothing) — never a fabricated badge."""
    from agenttic.certification.staleness import status
    for row in rows:
        row["certification"] = None
        try:
            d = reg.latest_dossier(row["agent_id"])
        except Exception:  # noqa: BLE001 — no dossier → uncertified
            continue
        row["certification"] = {
            "tier": d.tier_decision.tier,
            "attestation": d.attestation.mode,
            "status": status(reg, d),
            "dossier_id": d.dossier_id,
        }
