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

# Public, UNAUTHENTICATED dossier verification (SPEC-2 T18.1). Renders entirely
# from the persisted dossier JSON: offline hash verification + computed status.
public_router = APIRouter(tags=["certification-public"])


def public_dossier_view(reg, dossier_id: str) -> dict:
    """Build the public verification view from the dossier JSON alone: the
    dossier, its offline hash-verification result, and its computed status.
    Pure enough to snapshot with an otherwise-empty registry."""
    from ascore.certification.dossier import verify_dossier
    from ascore.certification.staleness import status, status_reasons
    d = reg.get_dossier(dossier_id)
    v = verify_dossier(d, reg)
    return {
        "dossier": d.model_dump(mode="json"),
        "verified": v.ok,
        "problems": v.problems,
        "status": status(reg, d),
        "status_reasons": status_reasons(reg, d),
        "tier": d.tier_decision.tier,
        "attestation": d.attestation.mode,
    }


@public_router.get("/certification/{dossier_id}")
def public_certification(dossier_id: str, request: Request):
    reg = request.app.state.reg
    try:
        view = public_dossier_view(reg, dossier_id)
    except NotFoundError:
        raise HTTPException(404, f"dossier {dossier_id} not found")
    return view


def public_card_view(reg, agent_id: str) -> dict:
    """Public card view rendered from the card JSON alone: fields grouped by
    provenance class (measured/documented/attested/none) + per-category
    completeness. Provenance classes are surfaced so the UI can style them
    distinctly."""
    from ascore.cards.fields import card_completeness
    card = reg.get_card(agent_id)
    by_provenance = {"measured": [], "documented": [], "attested": [],
                     "none_found": [], "confirmed_none": [], "not_applicable": []}
    for key, fv in card.fields.items():
        bucket = fv.provenance if fv.status == "value_present" else fv.status
        by_provenance.setdefault(bucket, []).append({
            "field_key": key, "value": fv.value, "status": fv.status,
            "provenance": fv.provenance,
            "refs": list(fv.evidence_refs or fv.citations)})
    return {
        "agent_id": card.agent_id,
        "version": card.version,
        "source": card.source,
        "attribution": card.attribution,
        "completeness": card_completeness(card),
        "fields_by_provenance": by_provenance,
    }


@public_router.get("/cards/{agent_id:path}")
def public_card(agent_id: str, request: Request):
    reg = request.app.state.reg
    try:
        return public_card_view(reg, agent_id)
    except NotFoundError:
        raise HTTPException(404, f"card {agent_id} not found")


@public_router.get("/catalog")
def public_catalog(request: Request, source: str | None = None):
    """The Catalog: all agent cards (optionally filtered by source). Index-imported
    agents live here; they never appear on score leaderboards (Hard Rule 17)."""
    reg = request.app.state.reg
    return {"cards": reg.list_cards(source=source)}


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
        tenant=getattr(state, "tenant", "default"),
        role=getattr(state, "role", None))
    return {"job_id": job_id}


@router.get("/certify/jobs/{job_id}")
def certify_job(job_id: str, request: Request):
    job = request.state.certifier.get(job_id)
    if job is None:
        raise HTTPException(404, f"certify job {job_id} not found")
    return job


@router.get("/dossiers")
def list_dossiers(request: Request, agent_id: str | None = None):
    from ascore.certification.staleness import status
    reg = request.state.reg
    rows = reg.list_dossiers(agent_id)
    for row in rows:
        try:
            row["status"] = status(reg, reg.get_dossier(row["dossier_id"]))
        except Exception:  # noqa: BLE001
            row["status"] = "current"
    return rows


@router.get("/dossiers/{dossier_id}")
def get_dossier(dossier_id: str, request: Request):
    from ascore.certification.staleness import status, status_reasons
    try:
        d = request.state.reg.get_dossier(dossier_id)
    except NotFoundError:
        raise HTTPException(404, f"dossier {dossier_id} not found")
    body = d.model_dump(mode="json")
    body["status"] = status(request.state.reg, d)
    body["status_reasons"] = status_reasons(request.state.reg, d)
    return body


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
