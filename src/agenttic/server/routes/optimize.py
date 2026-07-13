"""Prompt-optimization endpoints — start a self-improving system-prompt run,
poll its status, and fetch the result (best prompt + train/heldout improvement +
the round-by-round lineage). Auth + tenant scoped like every other run; the loop
executes the suite many times with the tenant's OWN Anthropic key (BYO-key), and
the response is explicit that optimization is a multi-run, cost-bearing job."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agenttic.optimizer import project_runs
from agenttic.registry.sqlite_store import NotFoundError
from agenttic.server.auth import require_operator
from agenttic.server.keys import tenant_run_clients

router = APIRouter(tags=["optimize"])


class OptimizeRequest(BaseModel):
    agent_id: str = "agent-under-test"
    suite_id: str
    version: int | None = None
    baseline_prompt: str = ""
    rounds: int = 2
    candidates_per_round: int = 3
    heldout_fraction: float = 0.3
    seed: int = 1234
    variant: str = "reference"
    model: str = ""
    url: str = ""
    max_agent_runs: int = 60


@router.post("/optimize/runs", dependencies=[Depends(require_operator)])
async def start_optimize(body: OptimizeRequest, request: Request):
    """Launch a prompt-optimization run (async). Returns immediately with a
    run_id to poll, plus the projected number of suite executions so the cost is
    clear up front. 400 if the tenant has no Anthropic key set."""
    state = request.state
    try:
        suite, _cases = state.reg.get_suite(body.suite_id, body.version)
    except NotFoundError:
        raise HTTPException(404, f"suite {body.suite_id} not found")
    if not suite.approved:
        raise HTTPException(400, f"suite {body.suite_id} is not approved — "
                                 "approve it before optimizing against it")
    clients = tenant_run_clients(request)  # tenant key (or None for injected)
    has_heldout = body.heldout_fraction > 0 and len(suite.test_ids) > 1
    projected = project_runs(
        max(1, min(body.rounds, 10)),
        max(1, min(body.candidates_per_round, 8)), has_heldout)
    run_id = state.optimizer.start(
        body.agent_id, body.suite_id, rounds=body.rounds,
        candidates_per_round=body.candidates_per_round,
        heldout_fraction=body.heldout_fraction, seed=body.seed,
        baseline_prompt=body.baseline_prompt, model=body.model,
        variant=body.variant, url=body.url, version=body.version,
        max_agent_runs=body.max_agent_runs, clients=clients)
    return {"run_id": run_id, "projected_agent_runs": projected,
            "max_agent_runs": body.max_agent_runs,
            "note": "Optimization runs your suite many times with your own key; "
                    "projected_agent_runs is the upper bound on suite executions."}


@router.get("/optimize/runs")
def list_optimize_runs(request: Request, agent_id: str | None = None,
                       suite_id: str | None = None):
    return {"runs": request.state.reg.list_optimization_runs(agent_id, suite_id)}


@router.get("/optimize/runs/{run_id}")
def get_optimize_run(run_id: str, request: Request):
    """Status + the optimization artifact (null while running) + live progress."""
    try:
        run = request.state.reg.get_optimization_run(run_id)
    except NotFoundError:
        raise HTTPException(404, f"optimization run {run_id} not found")
    run["progress"] = request.state.optimizer.progress(run_id)
    return run
