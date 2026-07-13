"""Hardening loop endpoints — promote caught failures into a per-agent
regression suite, list/inspect those suites, and re-run them to prove a fix
held (with a per-case regression delta). Auth + tenant scoped like every other
run; re-runs execute with the tenant's own Anthropic key (BYO-key)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agenttic import hardening
from agenttic.registry.sqlite_store import NotFoundError
from agenttic.server.auth import require_operator
from agenttic.server.keys import build_tenant_clients
from agenttic.server.keys import KeyStore

router = APIRouter(tags=["hardening"])
logger = logging.getLogger(__name__)


def _tenant_clients(request: Request) -> dict:
    """Clients for a hardening re-run: injected (test/dev) win; else build from
    the tenant's own key. 400 if the tenant has no key set."""
    injected = getattr(request.state, "clients", None) or {}
    if injected:
        return injected
    reg, cfg = request.state.reg, request.state.cfg
    key = KeyStore(reg.engine, cfg).get_key(getattr(request.state, "tenant", "default"))
    if not key:
        raise HTTPException(400, "Add your Anthropic API key in Settings to "
                                 "re-run a regression suite.")
    return build_tenant_clients(key)


class PromoteBody(BaseModel):
    # source="scorecard" (default): promote a scorecard's failing cases.
    source: str = "scorecard"
    scorecard_id: str | None = None    # required for source=scorecard
    test_ids: list[str] | None = None  # explicit subset; default = all failures
    # source="live": promote below-threshold live-monitor catches for an agent.
    agent_id: str | None = None        # required for source=live
    trace_ids: list[str] | None = None  # explicit subset of catches
    rubric_id: str = ""                # rubric the reconstructed cases run on
    threshold: float | None = None     # catch cutoff; default = library default


@router.post("/hardening/promote", dependencies=[Depends(require_operator)])
def promote(body: PromoteBody, request: Request):
    """Promote caught failures into the agent's regression suite — creating it
    or version-bumping an existing one. ``source=scorecard`` promotes a
    scorecard's failing cases; ``source=live`` promotes below-threshold
    live-monitor catches (reconstructed from production traces, needs-review)."""
    reg = request.state.reg
    try:
        if body.source == "live":
            if not body.agent_id:
                raise HTTPException(422, "source=live requires agent_id")
            kw = {} if body.threshold is None else {"threshold": body.threshold}
            return hardening.promote_live_failures_op(
                reg, body.agent_id, trace_ids=body.trace_ids,
                rubric_id=body.rubric_id, **kw)
        if not body.scorecard_id:
            raise HTTPException(422, "source=scorecard requires scorecard_id")
        return hardening.promote_failures_op(
            reg, body.scorecard_id, test_ids=body.test_ids, source=body.source)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc))


@router.get("/hardening/suites")
def list_suites(request: Request):
    """All regression suites in this workspace, with the latest delta summary."""
    return {"suites": hardening.list_regression_suites(request.state.reg)}


@router.get("/hardening/candidates")
def candidates(request: Request):
    """Scorecards with at least one failing case — the promotion sources."""
    return {"candidates": hardening.promotion_candidates(request.state.reg)}


@router.get("/hardening/live-candidates")
def live_candidates(request: Request, agent_id: str | None = None,
                    threshold: float | None = None):
    """Below-threshold live-monitor catches — promotable, distinct from
    scorecard candidates. ``agent_id`` narrows to one agent; ``threshold``
    overrides the default catch cutoff."""
    kw = {} if threshold is None else {"threshold": threshold}
    return {"candidates": hardening.live_catch_candidates(
        request.state.reg, agent_id, **kw)}


@router.get("/hardening/suites/{regression_suite_id}")
def suite_detail(regression_suite_id: str, request: Request):
    """One regression suite: cases (+ why promoted), history, latest delta."""
    try:
        return hardening.regression_detail(request.state.reg, regression_suite_id)
    except NotFoundError:
        raise HTTPException(404, f"regression suite {regression_suite_id} not found")


class RerunBody(BaseModel):
    regression_suite_id: str
    variant: str = "reference"
    url: str = ""
    system_prompt: str = ""
    model: str = ""
    managed_agent_id: str = ""
    environment_id: str = ""
    headers: dict | None = None


@router.post("/hardening/rerun", dependencies=[Depends(require_operator)])
async def rerun(body: RerunBody, request: Request):
    """Re-run a regression suite (background). The new scorecard + delta surface
    on the suite detail when done. Uses the tenant's own Anthropic key."""
    state = request.state
    try:
        state.reg.get_suite(body.regression_suite_id)
    except NotFoundError:
        raise HTTPException(404, f"regression suite {body.regression_suite_id} not found")
    clients = _tenant_clients(request)
    cfg, reg = state.cfg, state.reg
    client = clients.get("agent")
    judge = clients.get("judge") or client

    async def _bg():
        try:
            await hardening.rerun_regression_op(
                cfg, reg, body.regression_suite_id, variant=body.variant,
                url=body.url, system_prompt=body.system_prompt, model=body.model,
                managed_agent_id=body.managed_agent_id,
                environment_id=body.environment_id, headers=body.headers,
                client=client, judge_client=judge)
        except Exception as exc:  # noqa: BLE001
            logger.error("regression re-run failed for %s: %s",
                         body.regression_suite_id, exc)

    asyncio.create_task(_bg())
    return {"started": True, "regression_suite_id": body.regression_suite_id,
            "note": "Re-running the regression suite — the new scorecard and "
                    "regression delta appear on the suite detail when done."}
