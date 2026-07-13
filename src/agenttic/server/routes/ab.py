"""A/B comparison endpoints: start a head-to-head run of two variants on one
suite, poll its status, and fetch the comparison report (Markdown / PDF). Auth +
tenant scoped like every other run; both variants execute with the tenant's own
Anthropic key."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ascore import ops
from ascore.registry.sqlite_store import NotFoundError
from ascore.schema.ab import ABVariant
from ascore.server.auth import require_operator
from ascore.server.keys import tenant_run_clients

router = APIRouter(tags=["ab"])


class ABRunRequest(BaseModel):
    suite_id: str
    version: int | None = None
    variant_a: ABVariant
    variant_b: ABVariant


@router.post("/ab/runs", dependencies=[Depends(require_operator)])
async def start_ab_run(body: ABRunRequest, request: Request):
    """Launch an A/B run (async). Returns immediately with a comparison_id to
    poll. 400 if the tenant has no Anthropic key set."""
    state = request.state
    try:
        state.reg.get_suite(body.suite_id, body.version)
    except NotFoundError:
        raise HTTPException(404, f"suite {body.suite_id} not found")
    clients = tenant_run_clients(request)  # tenant key (or None for injected)
    comparison_id = state.ab.start(
        body.suite_id, body.variant_a, body.variant_b, body.version,
        clients=clients)
    return {"comparison_id": comparison_id}


@router.get("/ab/runs")
def list_ab_runs(request: Request, suite_id: str | None = None):
    return request.state.reg.list_ab_runs(suite_id)


@router.get("/ab/runs/{comparison_id}")
def get_ab_run(comparison_id: str, request: Request):
    """Status + the comparison artifact (null while running) + live progress."""
    try:
        run = request.state.reg.get_ab_run(comparison_id)
    except NotFoundError:
        raise HTTPException(404, f"ab comparison {comparison_id} not found")
    run["progress"] = request.state.ab.progress(comparison_id)
    return run


@router.get("/ab/runs/{comparison_id}/report", response_class=PlainTextResponse)
def ab_report(comparison_id: str, request: Request):
    try:
        return ops.ab_report_op(request.state.reg, comparison_id)
    except NotFoundError:
        raise HTTPException(404, f"ab comparison {comparison_id} not ready")


@router.get("/ab/runs/{comparison_id}/report.pdf")
def ab_report_pdf(comparison_id: str, request: Request):
    """The A/B comparison as a polished, on-brand PDF download."""
    try:
        pdf = ops.ab_report_pdf_op(request.state.reg, comparison_id)
    except NotFoundError:
        raise HTTPException(404, f"ab comparison {comparison_id} not ready")
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="ab-comparison-{comparison_id}.pdf"'})
