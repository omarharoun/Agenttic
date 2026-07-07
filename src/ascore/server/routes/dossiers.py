"""Certification + dossier endpoints (SPEC-2 T14.6).

* ``POST /api/certify`` — launch an async certification job (tenant + budget
  scoped; budgets are enforced inside the run pipeline). Returns a ``job_id``.
* ``GET  /api/certify/jobs/{job_id}`` — poll job status → dossier_id + tier.
* ``GET  /api/dossiers`` — list the tenant's dossiers.
* ``GET  /api/dossiers/{id}`` — the dossier JSON (verifiable artifact).
* ``GET  /api/dossiers/{id}/report.pdf`` — the dossier PDF.

All routes are tenant-scoped via ``request.state.reg``; the certify job runs with
the tenant's own Anthropic key (or the injected test client).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ascore.registry.sqlite_store import NotFoundError
from ascore.server.auth import require_operator
from ascore.server.keys import tenant_run_clients

router = APIRouter(tags=["certification"])


class CertifyRequest(BaseModel):
    agent_id: str = "ref-agent"
    profile_id: str = "cert-agent-safety-v1"
    variant: str = "reference"
    url: str = ""
    system_prompt: str = ""


@router.post("/certify", dependencies=[Depends(require_operator)])
async def start_certify(body: CertifyRequest, request: Request):
    """Launch a certification (async). 400 if the profile is undefined or the
    tenant has no Anthropic key (for a real reference/blackbox run)."""
    state = request.state
    defined = (state.cfg.get("certification", {}) or {}).get("profiles", {})
    if body.profile_id not in defined:
        raise HTTPException(404, f"profile {body.profile_id} not defined")
    clients = tenant_run_clients(request)  # tenant key (or None for injected)
    if clients is None:
        clients = getattr(state, "clients", None) or {}
    job_id = state.certifier.start(
        agent_id=body.agent_id, profile_id=body.profile_id, variant=body.variant,
        url=body.url, system_prompt=body.system_prompt, clients=clients or None,
        tenant=getattr(state, "tenant", "default"))
    return {"job_id": job_id}


@router.get("/certify/jobs/{job_id}")
def certify_job(job_id: str, request: Request):
    job = request.state.certifier.get(job_id)
    if job is None:
        raise HTTPException(404, f"certify job {job_id} not found")
    return job


@router.get("/dossiers")
def list_dossiers(request: Request, agent_id: str | None = None):
    return request.state.reg.list_dossiers(agent_id)


@router.get("/dossiers/{dossier_id}")
def get_dossier(dossier_id: str, request: Request):
    try:
        d = request.state.reg.get_dossier(dossier_id)
    except NotFoundError:
        raise HTTPException(404, f"dossier {dossier_id} not found")
    return d.model_dump(mode="json")


@router.get("/dossiers/{dossier_id}/report.pdf")
def dossier_report_pdf(dossier_id: str, request: Request):
    from ascore.reporting.dossier_report import render_pdf
    try:
        d = request.state.reg.get_dossier(dossier_id)
    except NotFoundError:
        raise HTTPException(404, f"dossier {dossier_id} not found")
    pdf = render_pdf(d)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f'inline; filename="{dossier_id}.pdf"'})
