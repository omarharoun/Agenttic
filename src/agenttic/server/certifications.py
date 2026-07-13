"""Certification persistence + issuance orchestration.

``CertStore`` is the data layer over the GLOBAL ``certifications`` table (where
issued certs live, like users/PATs): issue, get-by-id (public), list-for-tenant,
revoke, and signature verification. ``issue_certificate`` is the orchestrator
that turns a *completed safety scorecard* into a signed certificate — it enforces
the honesty contract: real results only, required safety dimensions present, and
the agent's ``config_hash`` pinned from the actual run trace.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlmodel import Session, select

from agenttic import certification as cert
from agenttic.registry.sqlite_store import CertificationRow, NotFoundError


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _config_hash_from_scorecard(reg, sc) -> str:
    """The agent ``config_hash`` that produced this scorecard — read from a run
    trace (which pins ``agent_config_hash``). Refuses if the scorecard has no
    retrievable trace: a certificate must be tied to a real, identifiable agent
    version, never issued blind."""
    for rs in sc.run_scores:
        try:
            trace = reg.get_trace(rs.trace_id)
        except NotFoundError:
            continue
        if trace.agent_config_hash:
            return trace.agent_config_hash
    raise cert.CertificationError(
        "cannot pin config_hash: no run trace found for this scorecard "
        "(certificates require a real, identifiable agent version)")


def _agent_name(reg, agent_id: str) -> str:
    try:
        agent = reg.get_declared_agent(agent_id)
        return (agent.description or agent.agent_id).strip() or agent_id
    except Exception:  # noqa: BLE001 — discovered (undeclared) agents are fine
        return agent_id


def issue_certificate(*, global_engine, cfg: dict, reg, tenant: str,
                      scorecard_id: str,
                      expires_days: int = cert.DEFAULT_EXPIRY_DAYS) -> dict:
    """Issue a signed safety certificate from a completed scorecard.

    Raises ``CertificationError`` (→ 422 at the route) if the scorecard isn't a
    safety run covering the required dimensions, or ``NotFoundError`` if it does
    not exist for this tenant. Returns the stored certificate dict."""
    try:
        sc = reg.get_scorecard(scorecard_id)
    except NotFoundError as exc:
        raise NotFoundError(f"scorecard {scorecard_id} not found") from exc

    if not sc.run_scores:
        raise cert.CertificationError(
            "scorecard has no run results — cannot certify without real results")

    dimension_scores = cert.extract_dimension_scores(sc.per_criterion_means)
    missing = cert.missing_required(dimension_scores)
    if missing:
        covered = sorted(dimension_scores) or ["none"]
        raise cert.CertificationError(
            "scorecard does not cover the required safety dimensions "
            f"{list(cert.REQUIRED_DIMENSIONS)} — missing {missing} "
            f"(covered: {covered}). Run the agenttic safety suites "
            "(AgentHarm refusal + AgentDojo/InjecAgent injection) and certify "
            "from a scorecard that measures them.")

    config_hash = _config_hash_from_scorecard(reg, sc)
    issued_at = _now()
    expires_at = cert.expiry_from(issued_at, expires_days)
    cert_id = "cert_" + uuid.uuid4().hex[:16]

    payload = cert.build_certificate_payload(
        cert_id=cert_id, agent_id=sc.agent_id,
        agent_name=_agent_name(reg, sc.agent_id), config_hash=config_hash,
        scorecard_id=scorecard_id, suite_id=sc.suite_id,
        suite_version=sc.suite_version, dimension_scores=dimension_scores,
        issued_at=issued_at, expires_at=expires_at)
    # Ed25519-sign: embeds signature_alg + public_key_id into the payload and
    # signs the canonical form. Fails closed in prod if no signing key is set.
    payload, signature = cert.sign_certificate(payload, cfg=cfg)

    row = CertificationRow(
        cert_id=cert_id, tenant_id=tenant, agent_id=sc.agent_id,
        config_hash=config_hash, scorecard_id=scorecard_id,
        grade=payload["grade"], payload=cert.canonical_json(payload),
        signature=signature, issued_at=issued_at, expires_at=expires_at,
        created_at=issued_at)
    with Session(global_engine) as s:
        s.add(row)
        s.commit()
    return CertStore(global_engine).public_view(cert_id, cfg=cfg)


class CertStore:
    """Reads / revoke / verify over the GLOBAL certifications table."""

    def __init__(self, engine):
        self.engine = engine

    def _row(self, cert_id: str) -> CertificationRow | None:
        with Session(self.engine) as s:
            return s.exec(select(CertificationRow).where(
                CertificationRow.cert_id == cert_id)).first()

    def _payload(self, row: CertificationRow) -> dict:
        import json
        return json.loads(row.payload)

    def public_view(self, cert_id: str, *, cfg: dict | None = None) -> dict:
        """The full public certificate view by id — grade, agent, per-dimension
        breakdown, dates, lifecycle status, and the signature-verification bool.
        Tenant-agnostic (anyone with the id can verify). Raises NotFoundError."""
        row = self._row(cert_id)
        if row is None:
            raise NotFoundError(f"certification {cert_id} not found")
        payload = self._payload(row)
        status = cert.certificate_status(payload, row.revoked_at)
        verified = cert.verify_signature(payload, row.signature, cfg=cfg)
        alg = payload.get("signature_alg")
        kid = payload.get("public_key_id")
        return {
            "cert_id": row.cert_id,
            "methodology_version": payload.get("methodology_version"),
            "agent_id": payload.get("agent_id"),
            "agent_name": payload.get("agent_name"),
            "config_hash": payload.get("config_hash"),
            "scorecard_id": payload.get("scorecard_id"),
            "suite_id": payload.get("suite_id"),
            "suite_version": payload.get("suite_version"),
            "grade": payload.get("grade"),
            "grade_band": payload.get("grade_band"),
            "grade_capped": payload.get("grade_capped"),
            "cap_reason": payload.get("cap_reason"),
            "composite_score": payload.get("composite_score"),
            "dimensions": payload.get("dimensions", []),
            "issued_at": payload.get("issued_at"),
            "expires_at": payload.get("expires_at"),
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "status": status,
            "valid": status == "valid" and verified,
            "signature": row.signature,
            "signature_alg": alg,
            "public_key_id": kid,
            # The exact bytes that were signed — so a third party can verify
            # offline without guessing our canonicalisation.
            "signed_payload": cert.canonical_json(payload),
            # signature_verified is the ISSUER's own check (convenience). The
            # trustworthy path is independent verification: see `verification`.
            "signature_verified": verified,
            "verification": {
                "algorithm": alg or "hmac-sha256 (legacy)",
                "public_key_id": kid,
                "public_keys_url": "/.well-known/agenttic-cert-keys.json",
                "how": ("Independently verify WITHOUT trusting Agenttic: fetch "
                        "the public key for `public_key_id` from "
                        "`public_keys_url`, then Ed25519-verify `signature` "
                        "(base64) over `signed_payload`. No secret needed."),
            },
            "note": ("This certificate is bound to the agent's config_hash. If "
                     "the agent's configuration changes, its config_hash changes "
                     "and this certificate no longer describes the running agent "
                     "— re-certify the new version."),
        }

    def verify(self, cert_id: str, *, cfg: dict | None = None) -> dict:
        """Just the signature-verification result + lifecycle status (cheap,
        cache-friendly). Raises NotFoundError."""
        row = self._row(cert_id)
        if row is None:
            raise NotFoundError(f"certification {cert_id} not found")
        payload = self._payload(row)
        verified = cert.verify_signature(payload, row.signature, cfg=cfg)
        status = cert.certificate_status(payload, row.revoked_at)
        return {
            "cert_id": cert_id,
            "signature_verified": verified,
            "signature": row.signature,
            "signature_alg": payload.get("signature_alg"),
            "public_key_id": payload.get("public_key_id"),
            "signed_payload": cert.canonical_json(payload),
            "public_keys_url": "/.well-known/agenttic-cert-keys.json",
            "status": status,
            "valid": status == "valid" and verified,
            "grade": payload.get("grade"),
            "config_hash": payload.get("config_hash"),
            "methodology_version": payload.get("methodology_version"),
            "config_hash_note": ("The grade applies only to the agent version "
                                 "with this config_hash; a changed agent must be "
                                 "re-certified."),
        }

    def badge(self, cert_id: str, *, cfg: dict | None = None) -> str:
        """The shields.io-style SVG badge for a cert. A revoked/expired cert
        renders accordingly; a cert whose signature fails verification renders
        ``unverified`` (never a clean grade). Raises NotFoundError."""
        row = self._row(cert_id)
        if row is None:
            raise NotFoundError(f"certification {cert_id} not found")
        payload = self._payload(row)
        status = cert.certificate_status(payload, row.revoked_at)
        verified = cert.verify_signature(payload, row.signature, cfg=cfg)
        return cert.render_badge_svg(payload.get("grade", "F"), status,
                                     verified=verified)

    def list_for_tenant(self, tenant: str, *, cfg: dict | None = None
                        ) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.exec(select(CertificationRow).where(
                CertificationRow.tenant_id == tenant
            ).order_by(CertificationRow.created_at.desc())).all()
        out = []
        for r in rows:
            payload = self._payload(r)
            status = cert.certificate_status(payload, r.revoked_at)
            out.append({
                "cert_id": r.cert_id, "agent_id": r.agent_id,
                "agent_name": payload.get("agent_name"),
                "config_hash": r.config_hash, "scorecard_id": r.scorecard_id,
                "grade": r.grade, "composite_score": payload.get("composite_score"),
                "issued_at": r.issued_at.isoformat(),
                "expires_at": r.expires_at.isoformat(),
                "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
                "status": status,
            })
        # Supersession: only the LATEST certificate per (agent, version) is
        # "current"; older certs for the same agent+config_hash are superseded.
        # This is what stops the UI from showing the same agent as two equally
        # "valid" but conflicting grades. `rows` is created_at desc, so the first
        # occurrence of each (agent_id, config_hash) is current, the rest not.
        seen: set[tuple[str, str]] = set()
        for c in out:
            key = (c["agent_id"], c["config_hash"] or "")
            c["superseded"] = key in seen
            c["current"] = key not in seen
            seen.add(key)
        return out

    def revoke(self, *, tenant: str, cert_id: str) -> bool:
        """Revoke a cert owned by ``tenant`` (immediate). Returns False if it's
        unknown, already revoked, or belongs to another tenant (no cross-tenant
        revocation)."""
        with Session(self.engine) as s:
            row = s.exec(select(CertificationRow).where(
                CertificationRow.cert_id == cert_id)).first()
            if (row is None or row.tenant_id != tenant
                    or row.revoked_at is not None):
                return False
            row.revoked_at = _now()
            s.add(row)
            s.commit()
            return True
