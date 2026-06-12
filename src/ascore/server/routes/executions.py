"""Execution lifecycle: start, inspect, SSE event stream, approve the human
gate, cancel, resume. The SSE stream replays persisted events after the
client's last seen seq (``?after=`` or the EventSource Last-Event-ID header),
then follows live."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ascore.registry.sqlite_store import NotFoundError
from ascore.server.executor import WorkflowValidationError

router = APIRouter(tags=["executions"])


@router.post("/workflows/{workflow_id}/executions")
async def start_execution(workflow_id: str, request: Request):
    # async: the manager calls asyncio.create_task, which needs the loop
    state = request.app.state
    try:
        wf = state.store.get_workflow(workflow_id)
    except NotFoundError:
        raise HTTPException(404, f"workflow {workflow_id} not found")
    try:
        execution_id = state.manager.start(wf)
    except WorkflowValidationError as exc:
        raise HTTPException(422, detail={"problems": exc.problems})
    return {"execution_id": execution_id}


@router.get("/executions")
def list_executions(request: Request, workflow_id: str | None = None):
    return request.app.state.store.list_executions(workflow_id)


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str, request: Request):
    try:
        return request.app.state.store.get_execution(execution_id)
    except NotFoundError:
        raise HTTPException(404, f"execution {execution_id} not found")


@router.get("/executions/{execution_id}/events")
async def stream_events(execution_id: str, request: Request, after: int = 0):
    state = request.app.state
    try:
        state.store.get_execution(execution_id)
    except NotFoundError:
        raise HTTPException(404, f"execution {execution_id} not found")
    last_id = request.headers.get("last-event-id")
    if last_id and last_id.isdigit():
        after = max(after, int(last_id))

    async def gen():
        async for evt in state.bus.subscribe(execution_id, after=after):
            yield {"id": str(evt["seq"]), "event": evt["type"],
                   "data": json.dumps({"node_id": evt["node_id"],
                                       "data": evt["data"],
                                       "seq": evt["seq"]})}
        yield {"event": "stream_end", "data": "{}"}

    return EventSourceResponse(gen())


@router.post("/executions/{execution_id}/approve")
async def approve_execution(execution_id: str, request: Request):
    """Approve the suite a gated execution is waiting on, then release the
    gate (in-process) or resume (after a server restart)."""
    state = request.app.state
    try:
        ex = state.store.get_execution(execution_id)
    except NotFoundError:
        raise HTTPException(404, f"execution {execution_id} not found")
    if ex["status"] != "waiting_approval":
        raise HTTPException(409, f"execution is {ex['status']}, not waiting")
    waiting = [e for e in state.store.events_after(execution_id)
               if e["type"] == "node_waiting"]
    if not waiting:
        raise HTTPException(409, "no node_waiting event recorded")
    info = waiting[-1]["data"]
    state.reg.approve_suite(info["suite_id"], info["version"])
    if not state.manager.approve_gate(execution_id):
        state.manager.resume(execution_id)
    return {"approved": {"suite_id": info["suite_id"],
                         "version": info["version"]}}


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str, request: Request):
    await request.app.state.manager.cancel(execution_id)
    return {"cancelled": execution_id}


@router.post("/executions/{execution_id}/resume")
async def resume_execution(execution_id: str, request: Request):
    try:
        request.app.state.manager.resume(execution_id)
    except (NotFoundError, ValueError) as exc:
        raise HTTPException(409, str(exc))
    return {"resumed": execution_id}
