"""Agent Safety Certification endpoints.

Two routers:

* **Authed + tenant-scoped** (``/api/certifications``) — issue a certificate from
  a completed safety scorecard, list the tenant's certs, and revoke one. Mounted
  under the standard auth + workspace binding.

* **Public, no auth** (``/api/public/certifications``) — the verification surface
  that powers the public "Tested with Agenttic" page: fetch a certificate by id,
  verify its signature, and render an embeddable SVG badge. Looked up by id
  regardless of tenant; cache-friendly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ascore import certification as cert
from ascore.registry.sqlite_store import NotFoundError
from ascore.server.auth import require_operator
from ascore.server.certifications import CertStore, issue_certificate

# ----------------------------------------------------------------------------- #
# Authenticated, tenant-scoped router.
# ----------------------------------------------------------------------------- #

router = APIRouter(tags=["certifications"])


class IssueBody(BaseModel):
    scorecard_id: str
    expires_days: int = cert.DEFAULT_EXPIRY_DAYS


@router.post("/certifications", dependencies=[Depends(require_operator)])
def issue(body: IssueBody, request: Request):
    """Issue a signed safety certificate from a completed safety scorecard. The
    scorecard must cover the required safety dimensions (refusal + injection);
    otherwise this returns 422 with a clear explanation. The certificate pins the
    agent's config_hash and is signed (tamper-evident)."""
    if not body.scorecard_id.strip():
        raise HTTPException(422, "scorecard_id is required")
    global_engine = request.app.state.reg.engine
    try:
        return issue_certificate(
            global_engine=global_engine, cfg=request.state.cfg,
            reg=request.state.reg, tenant=request.state.tenant,
            scorecard_id=body.scorecard_id.strip(),
            expires_days=body.expires_days)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc))
    except cert.CertificationError as exc:
        raise HTTPException(422, str(exc))


@router.get("/certifications")
def list_certifications(request: Request):
    """Every certificate issued by the caller's tenant (newest first)."""
    store = CertStore(request.app.state.reg.engine)
    return {"certifications": store.list_for_tenant(request.state.tenant,
                                                    cfg=request.state.cfg)}


@router.delete("/certifications/{cert_id}",
               dependencies=[Depends(require_operator)])
def revoke(cert_id: str, request: Request):
    """Revoke a certificate the tenant owns (immediate; revoked certs verify as
    'revoked'). 404 if it isn't the tenant's or is already revoked."""
    store = CertStore(request.app.state.reg.engine)
    if not store.revoke(tenant=request.state.tenant, cert_id=cert_id):
        raise HTTPException(404, f"certification {cert_id} not found, already "
                                 "revoked, or not owned by this tenant")
    return {"cert_id": cert_id, "status": "revoked"}


# ----------------------------------------------------------------------------- #
# Public, unauthenticated verification router.
# ----------------------------------------------------------------------------- #

public_router = APIRouter(tags=["certifications-public"])

# Public reads are immutable enough to cache briefly; the badge a bit longer.
_CACHE = "public, max-age=300"
_BADGE_CACHE = "public, max-age=600"


def _store(request: Request) -> CertStore:
    # The certifications table is GLOBAL (default-tenant engine in SQLite, the
    # shared engine in Postgres) — same place users/PATs live.
    return CertStore(request.app.state.reg.engine)


def _cfg(request: Request) -> dict:
    return request.app.state.cfg


@public_router.get("/public/calibration")
def public_calibration(request: Request):
    """The DEMONSTRATED calibration of Agenttic's deterministic heuristic checks
    against the shipped human-label corpus — per-criterion agreement, which
    criteria clear the bar, and the intentional tail disagreements. Powers an
    honest calibration disclosure on the Methodology page (no more unproven
    "calibrated"). No auth. The LLM judge is not covered here and stays
    provisional — stated in the payload's note."""
    from ascore.scoring.corpus import run_corpus_calibration
    try:
        return JSONResponse(run_corpus_calibration().to_dict(),
                            headers={"Cache-Control": _CACHE})
    except Exception as exc:  # noqa: BLE001 — a public read must never 500
        return JSONResponse(
            {"error": f"calibration corpus unavailable: {exc}"}, status_code=503)


@public_router.get("/public/reproduction")
def public_reproduction(request: Request):
    """Per-wedge HONEST reproduction status: whether each wedge reproduces a
    public benchmark number, runs a proxy, or demonstrates the methodology on a
    seed sample — and what real reproduction would take. Lets the UI stop hiding
    the SWE-bench-proxy / seed-sample caveats the docs already admit. No auth."""
    from ascore.metrics.reproduction import reproduction_report
    return JSONResponse(reproduction_report(),
                        headers={"Cache-Control": _CACHE})


@public_router.get("/public/redteam/injection")
def public_redteam_injection(request: Request):
    """The red-team prompt-injection probe set + an HONEST self-test of the
    lexical injection detector: technique coverage, and how many real hijacks the
    heuristic catches vs misses (the evasion tail). Lets the UI stop pretending
    the safety check is airtight. No auth."""
    from ascore.metrics.redteam import (
        INJECTION_PROBES,
        INJECTION_TECHNIQUES,
        evaluate_injection_detector,
        technique_counts,
    )
    try:
        detector = evaluate_injection_detector().to_dict()
    except Exception as exc:  # noqa: BLE001 — public read must never 500
        detector = {"error": f"detector self-test unavailable: {exc}"}
    return JSONResponse(
        {"suite_id": "redteam-injection-v1",
         "n_probes": len(INJECTION_PROBES),
         "techniques": INJECTION_TECHNIQUES,
         "technique_counts": technique_counts(),
         "detector_self_test": detector},
        headers={"Cache-Control": _CACHE})


@public_router.get("/public/certifications/keys")
def public_keys(request: Request):
    """The published Ed25519 public keys certificates are signed with, so anyone
    can verify a certificate WITHOUT trusting Agenttic (fetch the key for a
    cert's ``public_key_id``, then Ed25519-verify its signature over
    ``signed_payload``). Also served at ``/.well-known/agenttic-cert-keys.json``.
    No auth. Never exposes the private key."""
    return JSONResponse(
        {"alg": cert.SIGNATURE_ALG,
         "keys": cert.published_public_keys(_cfg(request))},
        headers={"Cache-Control": _CACHE})


@public_router.get("/public/certifications/{cert_id}")
def public_get(cert_id: str, request: Request):
    """The public certificate: grade, agent, the real per-dimension safety
    breakdown, methodology version, issue/expiry/revocation status, and whether
    the signature verifies. No auth."""
    try:
        view = _store(request).public_view(cert_id, cfg=_cfg(request))
    except NotFoundError:
        raise HTTPException(404, f"certification {cert_id} not found")
    return JSONResponse(view, headers={"Cache-Control": _CACHE})


@public_router.get("/public/certifications/{cert_id}/verify")
def public_verify(cert_id: str, request: Request):
    """Signature-verification + lifecycle status only (lightweight). No auth."""
    try:
        result = _store(request).verify(cert_id, cfg=_cfg(request))
    except NotFoundError:
        raise HTTPException(404, f"certification {cert_id} not found")
    return JSONResponse(result, headers={"Cache-Control": _CACHE})


@public_router.get("/public/certifications/{cert_id}/badge.svg")
def public_badge(cert_id: str, request: Request):
    """An embeddable shields.io-style SVG badge ("Agenttic Safety: A"), coloured
    by grade. Revoked/expired/tampered certs render accordingly. No auth."""
    try:
        svg = _store(request).badge(cert_id, cfg=_cfg(request))
    except NotFoundError:
        raise HTTPException(404, f"certification {cert_id} not found")
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": _BADGE_CACHE})


#: The dogfood agent whose grade backs the public assistant seal.
SAFE_ASSISTANT_AGENT_ID = "safe-reference-assistant"


@public_router.get("/public/assistant/certification")
def public_assistant_certification(request: Request):
    """The Safe Reference Assistant's REAL safety grade + certificate id (its
    latest VALID cert), or a null grade if none has been issued. Powers the
    honest seal on the public assistant page + landing — a grade renders only
    when a real, verifiable certificate backs it (never a placeholder). No auth."""
    grade = cert_id = composite = None
    try:
        for c in _store(request).list_for_tenant("default", cfg=_cfg(request)):
            if c["agent_id"] == SAFE_ASSISTANT_AGENT_ID and c["status"] == "valid":
                grade, cert_id, composite = (
                    c["grade"], c["cert_id"], c.get("composite_score"))
                break  # list is created_at desc → the first valid is the latest
    except Exception:  # noqa: BLE001 — a public read must never 500
        pass
    return JSONResponse(
        {"agent_id": SAFE_ASSISTANT_AGENT_ID, "grade": grade, "cert_id": cert_id,
         "composite_score": composite, "gradeable": grade is not None},
        headers={"Cache-Control": _CACHE})
