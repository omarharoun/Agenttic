"""Union passport — sign one honest attestation over many evaluator sources.

Takes an :class:`~agenttic.evaluators.orchestrator.AggregateReport` and produces
a single Ed25519-signed certificate payload that attests to the UNION of its
sources. Reuses the existing signing path verbatim
(:func:`~agenttic.certification.safety_cert.sign_certificate` /
:func:`~agenttic.certification.safety_cert.verify_certificate`) — the signing
key and its ``kid`` are unchanged, so old dossiers keep verifying under the same
code. Only the payload SHAPE is new (and additive).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agenttic.certification.safety_cert import (
    build_multi_source_certificate_payload,
    expiry_from,
    published_public_keys,
    sign_certificate,
    verify_certificate,
)
from agenttic.evaluators.orchestrator import AggregateReport


@dataclass(frozen=True)
class UnionPassport:
    """A signed, third-party-verifiable union attestation."""

    signed_payload: dict[str, Any]
    signature: str
    public_key_id: str

    def verify(self, *, cfg: dict | None = None) -> bool:
        """Third-party verification against the published key alone (no secret)."""
        pub_b64 = None
        for entry in published_public_keys(cfg):
            if entry["kid"] == self.public_key_id:
                pub_b64 = entry["public_key_b64"]
                break
        if pub_b64 is None:
            return False
        return verify_certificate(self.signed_payload, self.signature, pub_b64)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signed_payload": self.signed_payload,
            "signature": self.signature,
            "public_key_id": self.public_key_id,
        }


def _deterministic_cert_id(report: AggregateReport) -> str:
    seed = report.config_hash + "|" + "|".join(
        sorted(f"{sr.source}@{sr.source_version}" for sr in report.per_source))
    return "cert-union-" + hashlib.sha256(seed.encode()).hexdigest()[:12]


def build_union_passport(
    report: AggregateReport, *, agent_name: str | None = None,
    cert_id: str | None = None, days: int = 90, cfg: dict | None = None,
    issued_at: datetime | None = None,
) -> UnionPassport:
    """Build and sign a union passport from an aggregate report."""
    issued = issued_at or datetime.now(timezone.utc)
    expires = expiry_from(issued, days)

    sources = [{"source": sr.source, "source_version": sr.source_version,
                "source_license": sr.source_license} for sr in report.per_source]
    attribution = [{"source": sr.source, "license": sr.source_license}
                   for sr in report.per_source]
    overall, breakdown = report.index_with_breakdown()

    payload = build_multi_source_certificate_payload(
        cert_id=cert_id or _deterministic_cert_id(report),
        agent_id=report.agent_id,
        agent_name=agent_name or report.agent_id,
        agent_version=report.agent_version,
        config_hash=report.config_hash,
        issued_at=issued, expires_at=expires,
        sources=sources,
        coverage=report.coverage,
        per_source_index=breakdown["per_source_index"],
        overall_index=overall,
        attribution=attribution,
        deployment_mode=report.deployment_mode,
        gate_decisions=[g.to_dict() for g in report.gate_decisions],
        dimension_vocab_version=report.dimension_vocab_version,
    )
    signed, signature = sign_certificate(payload, cfg=cfg)
    return UnionPassport(signed_payload=signed, signature=signature,
                         public_key_id=signed["public_key_id"])
