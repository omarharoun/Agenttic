"""Evidence-manifest schema (SPEC-12 Step 54).

**The governing rule (Hard Rule 51): sign the evidence, never the verdict.** A
manifest attests *what was measured, under which conditions, by whom* — it must
never assert that an agent is "safe". A certificate that overclaims launders
risk, so every manifest carries its own ``scope_statement`` and
``limits_statement`` and states its signing tier (Hard Rule 55: local
self-attestation is never presented as third-party assurance).

The manifest is bound to an exact ``agent_config_hash`` (Hard Rule 53) — a
changed subject invalidates it by construction — it **expires** (Hard Rule 52),
and it is revocable/suspendable when drift is detected.

Canonical serialization: :func:`canonical_json` emits deterministic key order and
fixed-precision floats so the same evidence always hashes identically across
processes and machines.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

#: Signing tiers. ``local_self_attested`` proves INTEGRITY (nothing was altered)
#: — not neutrality. ``assurance`` is a neutral third-party attestation.
SigningTier = Literal["local_self_attested", "assurance"]

#: Lifecycle of a manifest as reported by verification.
ManifestStatus = Literal["valid", "expired", "suspended", "revoked", "invalid"]

CalibrationState = Literal["calibrated", "provisional", "uncalibrated"]
UserSource = Literal["real", "simulated"]

#: Claims no artifact may make (Hard Rule 51). Verification and rendering are
#: tested against this list.
BANNED_CLAIMS = (
    "is safe", "certified safe", "certified secure", "guaranteed safe",
    "proven safe", "verified safe", "guarantees safety", "guarantees security",
    "risk-free", "fully secure",
    # SPEC-13 Step 63 hardening: the formal layer is the easiest place in the
    # platform to overclaim, so the singular / adjectival variants are banned too.
    "guarantee safety", "guarantee security", "guaranteed secure",
    "provably safe", "provably secure", "completely safe", "totally secure",
)

# Fixed precision for every float in canonical form — floats must never hash
# differently because of repr drift across platforms.
_FLOAT_PRECISION = 6


def _canonicalize(value: Any) -> Any:
    """Recursively normalise a value for canonical serialization."""
    if isinstance(value, float):
        return round(value, _FLOAT_PRECISION)
    if isinstance(value, dict):
        return {k: _canonicalize(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def canonical_json(payload: dict) -> str:
    """Deterministic JSON: sorted keys, fixed-precision floats, no whitespace
    drift, UTC timestamps. The same evidence always produces the same bytes.

    Delegates the serialization to :func:`agenttic.certification.hashing.canonical_json`
    (the platform's existing dossier/passport canonicalizer) and adds only the
    normalization SPEC-12 requires on top — fixed-precision floats and UTC
    datetimes — so this does not become a third competing canonical form."""
    from agenttic.certification.hashing import canonical_json as _base
    return _base(_canonicalize(payload))


def content_hash(payload: dict) -> str:
    """sha256 over the canonical form."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class Subject(BaseModel):
    """What was measured. The config hash binds the manifest to an exact agent
    version (Hard Rule 53)."""

    agent_id: str
    agent_config_hash: str


class JudgeConfig(BaseModel):
    """The judge behind one criterion, and how well calibrated it is. An
    uncalibrated judge is recorded as such — never silently counted as certain."""

    criterion_id: str
    judge_config_id: str
    version: int = 1
    calibration_state: CalibrationState = "uncalibrated"
    alpha: float | None = None          # agreement with human reviewers
    human_ceiling: float | None = None  # the human-vs-human ceiling alpha is read against


class IntegrityGates(BaseModel):
    """SPEC-6 integrity gate outcomes. ``waived`` names any gate a human waived,
    so a waiver is always visible in the evidence."""

    oracle: bool = False
    dummy: bool = False
    exploit: bool = False
    waived: list[str] = Field(default_factory=list)


class Contamination(BaseModel):
    canary_status: str = "unknown"      # e.g. "clean" | "triggered" | "unknown"
    exposure_flag: bool = False         # the agent was plausibly exposed to the suite


class Environment(BaseModel):
    harness_version: str = ""
    schema_version: str = ""
    model_ids: list[str] = Field(default_factory=list)


class EvidenceManifest(BaseModel):
    """What was measured, under which conditions, by whom. Never a verdict."""

    manifest_id: str
    subject: Subject
    suite_id: str
    suite_version: int
    rubric_id: str
    rubric_version: int
    judge_configs: list[JudgeConfig] = Field(default_factory=list)
    k: int = 1                                   # trials per case
    integrity_gates: IntegrityGates = Field(default_factory=IntegrityGates)
    contamination: Contamination = Field(default_factory=Contamination)
    scorecard_hash: str
    visibility_tier: Literal["glass_box", "black_box"]
    user_source: UserSource = "simulated"
    environment: Environment = Field(default_factory=Environment)
    #: hash of the Agent BOM (SPEC-12 54.3); the ABOM is referenced, not inlined.
    abom_sha256: str | None = None
    #: hash of the SPEC-13 verification sign-off (coverage/assertions/formal/…).
    #: A certificate whose headline is a pass rate says so; one backed by a
    #: sign-off names it here.
    signoff_sha256: str | None = None
    issued_at: datetime
    expires_at: datetime                          # Hard Rule 52: never unbounded
    issuer: str
    signing_tier: SigningTier = "local_self_attested"
    scope_statement: str
    limits_statement: str

    @model_validator(mode="after")
    def _honest_by_construction(self) -> "EvidenceManifest":
        if not self.subject.agent_config_hash.strip():
            raise ValueError(
                "manifest requires a subject.agent_config_hash — a certificate "
                "not bound to an exact agent version is invalid (Hard Rule 53)")
        if self.expires_at <= self.issued_at:
            raise ValueError(
                "manifest expires_at must be after issued_at (Hard Rule 52: "
                "certificates expire)")
        if not self.scope_statement.strip() or not self.limits_statement.strip():
            raise ValueError(
                "manifest requires both scope_statement and limits_statement "
                "(Hard Rule 51: every certificate carries scope and limits)")
        blob = f"{self.scope_statement} {self.limits_statement}".lower()
        for claim in BANNED_CLAIMS:
            if claim in blob:
                raise ValueError(
                    f"manifest asserts a banned unbounded claim {claim!r} — sign "
                    "the evidence, never the verdict (Hard Rule 51)")
        return self

    def payload(self) -> dict:
        """The signable body: every field, canonically ordered."""
        return _canonicalize(self.model_dump(mode="json"))

    def manifest_hash(self) -> str:
        """Deterministic hash of the manifest body (excludes any signature)."""
        return content_hash(self.model_dump(mode="json"))

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.expires_at.astimezone(timezone.utc)


class SignedManifest(BaseModel):
    """A manifest plus its detached signature. The signature covers
    ``manifest_hash`` so verification can recompute it independently."""

    manifest: EvidenceManifest
    manifest_sha256: str
    signature: str                    # base64 Ed25519 signature over manifest_sha256
    kid: str                          # public key id
    algorithm: str = "ed25519"
    #: For the assurance tier: where a third party can verify without trusting us
    #: (Sigstore Rekor entry / transparency log URL).
    transparency_log_url: str | None = None

    def tier(self) -> SigningTier:
        return self.manifest.signing_tier


class RevocationEntry(BaseModel):
    """One append-only entry in the published revocation list. Drift revokes
    automatically (Hard Rule 52)."""

    manifest_id: str
    subject_config_hash: str
    status: Literal["suspended", "revoked"]
    reason: str
    recorded_at: datetime
    #: what triggered it — e.g. "drift:re_eval_request", "retire", "manual"
    source: str = "manual"


class RevocationList(BaseModel):
    """A signed, append-only revocation list relying parties can poll."""

    issuer: str
    updated_at: datetime
    entries: list[RevocationEntry] = Field(default_factory=list)

    def status_for(self, manifest_id: str) -> RevocationEntry | None:
        """Latest entry for a manifest, if any (last write wins)."""
        found = [e for e in self.entries if e.manifest_id == manifest_id]
        return found[-1] if found else None

    def content_sha256(self) -> str:
        return content_hash(self.model_dump(mode="json"))
