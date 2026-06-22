"""Hardening loop endpoints — promote caught failures into a per-agent
regression suite, list/inspect those suites, and re-run them to prove a fix
held (with a per-case regression delta). Auth + tenant scoped like every other
run; re-runs execute with the tenant's own Anthropic key (BYO-key)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ascore import hardening
from ascore.registry.sqlite_store import NotFoundError
from ascore.server.auth import require_operator
from ascore.server.keys import build_tenant_clients
from ascore.server.keys import KeyStore

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
    scorecard_id: str
    test_ids: list[str] | None = None  # explicit subset; default = all failures
    source: str = "scorecard"


@router.post("/hardening/promote", dependencies=[Depends(require_operator)])
def promote(body: PromoteBody, request: Request):
    """Promote a scorecard's failing (non-errored) cases into the agent's
    regression suite — creating it or version-bumping an existing one."""
    try:
        return hardening.promote_failures_op(
            request.state.reg, body.scorecard_id,
            test_ids=body.test_ids, source=body.source)
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
