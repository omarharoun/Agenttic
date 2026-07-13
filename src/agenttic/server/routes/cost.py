"""Pre-run cost estimation endpoints + a budget context the UI shows before
a run. Read-only; no spend happens here."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from agenttic.budget import budget_context, tenant_quota
from agenttic.cost import estimate_for_suite, estimate_for_workflow
from agenttic.registry.sqlite_store import NotFoundError

router = APIRouter(tags=["cost"])


@router.get("/quota")
def quota(request: Request):
    """This tenant's spend quota + current usage (today and rolling 30 days)."""
    reg = request.state.reg
    q = tenant_quota(request.state.cfg, reg.tenant)
    today = reg.spend_today()
    month = reg.spend_since_days(29)
    def _remaining(cap, spent):
        return None if not cap else round(max(0.0, cap - spent), 6)
    return {"tenant": reg.tenant,
            "daily_usd": q["daily_usd"], "monthly_usd": q["monthly_usd"],
            "spent_today_usd": round(today, 6), "spent_month_usd": round(month, 6),
            "remaining_daily_usd": _remaining(q["daily_usd"], today),
            "remaining_monthly_usd": _remaining(q["monthly_usd"], month)}


@router.get("/estimate")
def estimate_suite(request: Request, suite_id: str,
                   agent_id: str | None = None, agent_model: str | None = None,
                   with_judge: bool = True):
    """Projected cost of running ``suite_id`` against an agent (declared agent
    or, by default, the reference agent on the default model)."""
    state = request.state
    try:
        est = estimate_for_suite(state.cfg, state.reg, suite_id,
                                 agent_id=agent_id, agent_model=agent_model,
                                 with_judge=with_judge)
    except NotFoundError:
        raise HTTPException(404, f"suite {suite_id} not found")
    return {"estimate": est.model_dump(),
            "budget": budget_context(state.cfg, state.reg, est.projected_usd)}


@router.get("/workflows/{workflow_id}/estimate")
def estimate_workflow(workflow_id: str, request: Request):
    state = request.state
    try:
        wf = state.store.get_workflow(workflow_id)
    except NotFoundError:
        raise HTTPException(404, f"workflow {workflow_id} not found")
    try:
        est = estimate_for_workflow(state.cfg, state.reg, wf)
    except (ValueError, NotFoundError) as exc:
        raise HTTPException(422, str(exc))
    return {"estimate": est.model_dump(),
            "budget": budget_context(state.cfg, state.reg, est.projected_usd)}
