"""Quickstart convenience endpoint — generate a benchmark from a business
requirement and run it end-to-end in one call.

Hand-authoring the full canvas graph over curl is awkward, so this builds the
canonical guided pipeline (business_doc → generator → human_gate → agent →
run_suite → score → scorecard → report) server-side, persists it, and starts an
execution. The human gate still pauses for approval — poll the execution, then
``POST /api/executions/{id}/approve`` to continue. Runs use the tenant's own
Anthropic key (400 if unset).
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agenttic.server.auth import require_operator
from agenttic.server.executor import WorkflowValidationError
from agenttic.server.keys import tenant_run_clients as _run_clients
from agenttic.server.workflow_schema import (
    Workflow, WorkflowEdge, WorkflowNode, validate_workflow,
)

router = APIRouter(tags=["quickstart"])


class FromRequirementBody(BaseModel):
    requirement: str                      # the business requirement text
    agent_id: str = "agent-under-test"
    system_prompt: str = ""               # instructions for the reference agent
    model: str = ""                       # optional model override
    suite_id: str = ""                    # optional; derived from requirement if blank
    name: str = "Quickstart from requirement"
    refresh: bool = False                 # bypass the result cache, run fresh


def _build_workflow(body: FromRequirementBody) -> Workflow:
    # Deterministic suite id from the requirement text, so re-running the SAME
    # requirement reuses the generated suite (no re-generation) and lets the run
    # hit the result cache — instead of minting a fresh suite every time.
    suite_id = body.suite_id or (
        "req-" + hashlib.sha256(body.requirement.strip().encode()).hexdigest()[:10])
    nodes = [
        WorkflowNode(node_id="doc", type="business_doc",
                     config={"text": body.requirement}),
        WorkflowNode(node_id="gen", type="generator",
                     config={"suite_id": suite_id}),
        WorkflowNode(node_id="gate", type="human_gate"),
        WorkflowNode(node_id="agent", type="agent",
                     config={"variant": "reference", "agent_id": body.agent_id,
                             "system_prompt": body.system_prompt,
                             "model": body.model}),
        WorkflowNode(node_id="run", type="run_suite"),
        WorkflowNode(node_id="score", type="score"),
        WorkflowNode(node_id="card", type="scorecard"),
        WorkflowNode(node_id="rpt", type="report"),
    ]
    edges = [
        WorkflowEdge(edge_id="e1", source="doc", source_port="doc",
                     target="gen", target_port="doc"),
        WorkflowEdge(edge_id="e2", source="gen", source_port="suite",
                     target="gate", target_port="suite"),
        WorkflowEdge(edge_id="e3", source="gate", source_port="suite",
                     target="run", target_port="suite"),
        WorkflowEdge(edge_id="e4", source="agent", source_port="agent",
                     target="run", target_port="agent"),
        WorkflowEdge(edge_id="e5", source="run", source_port="run",
                     target="score", target_port="run"),
        WorkflowEdge(edge_id="e6", source="score", source_port="scored",
                     target="card", target_port="scored"),
        WorkflowEdge(edge_id="e7", source="card", source_port="scorecard",
                     target="rpt", target_port="scorecard"),
    ]
    return Workflow(workflow_id=f"wf-{uuid.uuid4().hex[:8]}", name=body.name,
                    nodes=nodes, edges=edges)


@router.post("/quickstart/from-requirement",
             dependencies=[Depends(require_operator)])
async def from_requirement(body: FromRequirementBody, request: Request,
                           force: bool = False):
    if not body.requirement.strip():
        raise HTTPException(422, "requirement text is required")
    state = request.state
    refresh = bool(force or body.refresh)
    wf = _build_workflow(body)
    problems = validate_workflow(wf)
    if problems:  # pragma: no cover - canonical graph is valid by construction
        raise HTTPException(500, detail={"problems": problems})
    # surfaces a clear 400 if the tenant hasn't set their Anthropic key
    clients = _run_clients(request)
    state.store.save_workflow(wf)
    try:
        execution_id = state.manager.start(wf, clients=clients, force=refresh)
    except WorkflowValidationError as exc:
        raise HTTPException(422, detail={"problems": exc.problems})
    return {"workflow_id": wf.workflow_id, "execution_id": execution_id,
            "suite_id": wf.nodes[1].config["suite_id"], "refresh": refresh,
            "note": "Generation runs (or is skipped if this requirement was "
                    "already generated), then the workflow pauses at the human "
                    "gate unless the suite is already approved. If an identical "
                    "run is cached, the Run Suite step is served from cache ($0, "
                    "no agent/judge calls). Poll GET /api/executions/{id}; on "
                    "'waiting_approval' POST /api/executions/{id}/approve. Pass "
                    "?force=true (or \"refresh\": true) to bypass the cache."}
