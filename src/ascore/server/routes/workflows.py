"""Workflow CRUD + the node-type catalog that drives the palette and the
config side-panel forms (each node type ships its pydantic JSON schema)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ascore.registry.sqlite_store import NotFoundError
from ascore.server.nodes import NODE_TYPES
from ascore.server.workflow_schema import Workflow, validate_workflow

router = APIRouter(tags=["workflows"])


@router.get("/node-types")
def node_types():
    return [{
        "type": s.type, "title": s.title, "category": s.category,
        "description": s.description,
        "inputs": s.inputs, "outputs": s.outputs,
        "config_schema": s.config_model.model_json_schema(),
    } for s in NODE_TYPES.values()]


@router.get("/workflows")
def list_workflows(request: Request):
    return request.app.state.store.list_workflows()


@router.post("/workflows")
def save_workflow(wf: Workflow, request: Request, dry_run: bool = False):
    """Persist a workflow document (or just validate it with ?dry_run=true —
    used by the UI to check imports before anything is saved)."""
    problems = validate_workflow(wf)
    if not dry_run:
        request.app.state.store.save_workflow(wf)
    return {"workflow_id": wf.workflow_id, "problems": problems,
            "saved": not dry_run}


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str, request: Request):
    try:
        wf = request.app.state.store.get_workflow(workflow_id)
    except NotFoundError:
        raise HTTPException(404, f"workflow {workflow_id} not found")
    return {"workflow": wf.model_dump(), "problems": validate_workflow(wf)}


@router.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: str, request: Request):
    request.app.state.store.delete_workflow(workflow_id)
    return {"deleted": workflow_id}
