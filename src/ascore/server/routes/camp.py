"""
Training Camp API — the folded-in AgentCamp training/eval layer.

Run a camp against a task (with the built-in deterministic baseline, or the
tenant's BYO-Anthropic-key agent as the thing under camp), read the results
(accuracy with the **Wilson 95% lower bound**, the two-condition **promotion
gate**, the graded-episode **memory**, and — for improve runs — the frozen-
holdout **ratchet** log + the human review queue), sign off on the gate as an
operator, and export the passing episodes as a **distillation dataset**.

All routes are auth-gated and tenant-scoped (mounted under the protected `/api`
routers in ``app.py``). Camp runs are deterministic and fast in ``mock`` mode, so
they run synchronously (off the event loop via a worker thread). ``agent`` mode
drives the real BYO-key agent and costs tokens, so episode counts are capped.

Honesty posture (kept from AgentCamp): the accuracy floor is non-overridable —
a human sign-off is required *on top of* clearing it, never a substitute — and
nothing here fabricates accuracy.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ascore.camp import service
from ascore.camp.trace import distillation_records
from ascore.registry.sqlite_store import NotFoundError
from ascore.server.auth import require_operator

router = APIRouter(tags=["camp"])

# Guard rails: mock runs are cheap and deterministic; BYO-agent runs spend the
# tenant's tokens, so cap them hard.
MAX_MOCK_EPISODES = 5000
MAX_AGENT_EPISODES = 200
MAX_IMPROVE_ROUNDS = 12
MAX_IMPROVE_EPISODES = 1000
MAX_HOLDOUT = 2000


def _approver_identity(request: Request) -> str:
    """The authenticated human behind a sign-off — an email when we have one,
    else a role/method label. Recorded so the gate's second condition is real."""
    email = getattr(request.state, "user_email", None)
    if email:
        return email
    role = getattr(request.state, "role", "operator")
    method = getattr(request.state, "auth_method", "token")
    return f"{role}:{method}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- request bodies -----------------------------------------------------------

class StartCampBody(BaseModel):
    task_id: str = "support_triage"
    mode: str = "mock"  # "mock" | "agent"
    episodes: int = Field(default=500, ge=1)
    threshold: float = Field(default=0.99, ge=0.0, le=1.0)
    min_episodes_for_gate: int = Field(default=200, ge=1)
    seed: int = 0
    # BYO-agent mode only:
    model: str = ""
    agent_id: str = ""


class ImproveCampBody(BaseModel):
    task_id: str = "support_triage"
    rounds: int = Field(default=5, ge=1)
    episodes_per_round: int = Field(default=300, ge=1)
    threshold: float = Field(default=0.99, ge=0.0, le=1.0)
    holdout: int = Field(default=600, ge=1)
    seed: int = 0
    degenerate: bool = False


# -- catalogue ----------------------------------------------------------------

@router.get("/camps/tasks")
def list_camp_tasks(request: Request):
    return {"tasks": service.available_tasks(), "modes": list(service.MODES)}


# -- start a single camp ------------------------------------------------------

@router.post("/camps", dependencies=[Depends(require_operator)])
async def start_camp(body: StartCampBody, request: Request):
    if body.task_id not in service.TASKS:
        raise HTTPException(400, f"unknown task '{body.task_id}'")
    if body.mode not in service.MODES:
        raise HTTPException(400, f"unknown mode '{body.mode}'")

    adapter = None
    if body.mode == "agent":
        if body.episodes > MAX_AGENT_EPISODES:
            raise HTTPException(
                400, f"agent mode is capped at {MAX_AGENT_EPISODES} episodes "
                     f"(it spends your Anthropic key); requested {body.episodes}")
        adapter = _build_byo_adapter(request, body)
    elif body.episodes > MAX_MOCK_EPISODES:
        raise HTTPException(400, f"episodes capped at {MAX_MOCK_EPISODES}")

    store = request.state.camp
    run_id = uuid4().hex[:12]
    store.create_run(
        run_id, kind="single", task_id=body.task_id, mode=body.mode,
        agent_label=body.agent_id or "", threshold=body.threshold,
        min_episodes_for_gate=body.min_episodes_for_gate, seed=body.seed)

    try:
        result = await asyncio.to_thread(
            service.run_single_camp,
            task_id=body.task_id, mode=body.mode, episodes=body.episodes,
            threshold=body.threshold, min_episodes_for_gate=body.min_episodes_for_gate,
            seed=body.seed, adapter=adapter)
    except Exception as exc:  # noqa: BLE001 — surface as run error, not a 500
        store.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise HTTPException(400, f"camp run failed: {exc}")

    report = result["report"]
    store.add_episodes(run_id, result["episodes"])
    store.finish_run(
        run_id, episodes=report["episodes"], passes=report["passes"],
        wilson_lower_95=report["wilson_lower_95"], pass_rate=report["pass_rate"],
        report=report, gate=result["gate"])
    return store.get_run(run_id)


def _build_byo_adapter(request: Request, body: StartCampBody):
    """Build a reference adapter driven by the tenant's own Anthropic key, with
    the task's system prompt so the agent emits the graded action schema."""
    from ascore.ops import build_adapter
    from ascore.server.keys import tenant_run_clients

    clients = tenant_run_clients(request)  # raises 400 if no key; None in tests
    client = (clients or {}).get("agent") if clients else \
        (request.state.clients or {}).get("agent")
    task = service.get_task(body.task_id)
    return build_adapter(
        request.state.cfg, variant="reference",
        agent_id=body.agent_id or "byo-agent", client=client,
        system_prompt=task.system_prompt(), model=body.model)


# -- start an improve loop ----------------------------------------------------

@router.post("/camps/improve", dependencies=[Depends(require_operator)])
async def start_improve(body: ImproveCampBody, request: Request):
    if body.task_id not in service.TASKS:
        raise HTTPException(400, f"unknown task '{body.task_id}'")
    if body.rounds > MAX_IMPROVE_ROUNDS:
        raise HTTPException(400, f"rounds capped at {MAX_IMPROVE_ROUNDS}")
    if body.episodes_per_round > MAX_IMPROVE_EPISODES:
        raise HTTPException(400, f"episodes_per_round capped at {MAX_IMPROVE_EPISODES}")
    if body.holdout > MAX_HOLDOUT:
        raise HTTPException(400, f"holdout capped at {MAX_HOLDOUT}")

    store = request.state.camp
    run_id = uuid4().hex[:12]
    store.create_run(
        run_id, kind="improve", task_id=body.task_id, mode="mock",
        agent_label="", threshold=body.threshold,
        min_episodes_for_gate=min(200, body.holdout), seed=body.seed)

    try:
        result = await asyncio.to_thread(
            service.run_improve_camp,
            task_id=body.task_id, rounds=body.rounds,
            episodes_per_round=body.episodes_per_round, threshold=body.threshold,
            holdout=body.holdout, seed=body.seed, degenerate=body.degenerate,
            approved_by=None)
    except Exception as exc:  # noqa: BLE001
        store.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise HTTPException(400, f"improve loop failed: {exc}")

    report = result["report"]
    store.add_episodes(run_id, result["episodes"])
    store.finish_run(
        run_id, episodes=report["episodes"], passes=report["passes"],
        wilson_lower_95=report["wilson_lower_95"], pass_rate=report["pass_rate"],
        report=report, gate=result["gate"], rounds=result["rounds"],
        review_queue=result["review_queue"])
    return store.get_run(run_id)


# -- read ---------------------------------------------------------------------

@router.get("/camps")
def list_camps(request: Request):
    return {"runs": request.state.camp.list_runs()}


@router.get("/camps/{run_id}")
def get_camp(run_id: str, request: Request):
    try:
        run = request.state.camp.get_run(run_id)
    except NotFoundError:
        raise HTTPException(404, f"camp run {run_id} not found")
    run["episode_sample"] = request.state.camp.episodes(run_id, limit=25)
    run["episode_count"] = request.state.camp.episode_count(run_id)
    run["distillation_count"] = len(
        request.state.camp.episodes(run_id, only_passing=True))
    return run


@router.get("/camps/{run_id}/episodes")
def get_camp_episodes(run_id: str, request: Request, limit: int = 200,
                      only_passing: bool | None = None):
    try:
        request.state.camp.get_run(run_id)
    except NotFoundError:
        raise HTTPException(404, f"camp run {run_id} not found")
    limit = max(1, min(limit, 1000))
    return {"episodes": request.state.camp.episodes(
        run_id, limit=limit, only_passing=only_passing)}


# -- the human sign-off (second, required condition of the gate) --------------

@router.post("/camps/{run_id}/approve", dependencies=[Depends(require_operator)])
def approve_camp(run_id: str, request: Request):
    store = request.state.camp
    try:
        row = store.get_run_row(run_id)
    except NotFoundError:
        raise HTTPException(404, f"camp run {run_id} not found")
    if row.status != "complete":
        raise HTTPException(422, f"run is {row.status}, cannot approve")

    from ascore.camp.trainer import CampReport
    report = CampReport(
        task_id=row.task_id, agent_id=row.agent_label or "agent",
        episodes=row.episodes, passes=row.passes, threshold=row.threshold,
        min_episodes_for_gate=row.min_episodes_for_gate)

    approver = _approver_identity(request)
    # The floor is re-checked here and is non-overridable: signing off on a run
    # that hasn't cleared it does NOT promote it.
    gate = service.evaluate_gate(report, approved_by=approver)
    approved_at = _now() if gate["floor_met"] else None
    store.set_gate(run_id, gate,
                   approved_by=approver if gate["floor_met"] else None,
                   approved_at=approved_at)
    return store.get_run(run_id)


# -- distillation export ------------------------------------------------------

@router.get("/camps/{run_id}/distillation.jsonl")
def export_distillation(run_id: str, request: Request):
    store = request.state.camp
    try:
        store.get_run(run_id)
    except NotFoundError:
        raise HTTPException(404, f"camp run {run_id} not found")

    def gen():
        for record in distillation_records(store.iter_episodes(run_id),
                                           only_passing=True):
            yield json.dumps(record, ensure_ascii=False) + "\n"

    headers = {"Content-Disposition":
               f'attachment; filename="camp-{run_id}-distillation.jsonl"'}
    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers=headers)
