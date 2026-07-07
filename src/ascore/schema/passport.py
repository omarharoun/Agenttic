"""Agent passport + action receipt schema (SPEC-2 M16, T31.1).

A **passport** is a short-lived, Ed25519-signed credential asserting an agent's
certification posture (tier, dossier hash, policy hash, stage, autonomy,
attestation) with an expiry, a status URL, and the key id that signed it. A
**receipt** binds a passport to a single allowed action (Hard Rule 29: receipts
require a logged allow-decision).

Verification is split from status (Hard Rule 28): a valid signature on a *revoked*
passport must be rejected — the status URL is checked separately.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class KeyRef(BaseModel):
    """A published verification key (a JWK-ish record)."""

    key_id: str                       # kid
    alg: str = "EdDSA"
    kty: str = "OKP"
    crv: str = "Ed25519"
    public_key_b64: str               # base64 of the 32-byte raw Ed25519 public key
    not_before: datetime | None = None
    not_after: datetime | None = None  # rotation overlap window end

    def jwk(self) -> dict:
        import base64
        # JWK uses base64url without padding for the x coordinate
        raw = base64.b64decode(self.public_key_b64)
        x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        return {"kty": self.kty, "crv": self.crv, "alg": self.alg,
                "kid": self.key_id, "x": x, "use": "sig"}


class PassportClaims(BaseModel):
    """The signed claim set. ``signing_input`` is what the signature covers."""

    agent_id: str
    tier: str
    dossier_sha256: str
    policy_hash: str
    stage: str = "internal"
    autonomy_level: str | None = None
    attestation_mode: str = "self_attested"
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    status_url: str
    key_id: str

    @model_validator(mode="after")
    def _tz(self) -> "PassportClaims":
        for attr in ("issued_at", "expires_at"):
            v = getattr(self, attr)
            if v is not None and v.tzinfo is None:
                setattr(self, attr, v.replace(tzinfo=timezone.utc))
        return self

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now >= self.expires_at


class Passport(BaseModel):
    """Signed passport: claims + detached Ed25519 signature (base64)."""

    passport_id: str
    claims: PassportClaims
    signature: str = ""
    status: Literal["active", "revoked"] = "active"  # local cache; status URL is truth

    def signing_input(self) -> dict:
        """The exact claim dict the signature covers (canonicalized by the signer)."""
        return self.claims.model_dump(mode="json")

    def ref(self) -> str:
        return f"passport:{self.passport_id}"


class Receipt(BaseModel):
    """A signed receipt binding a passport to one allowed action. No payloads by
    default — only input/output hashes (Hard Rule 30)."""

    receipt_id: str
    passport_id: str
    agent_id: str
    tool_call_ref: str
    action_class: str
    policy_hash: str
    decision_id: str
    input_sha256: str = ""
    output_sha256: str = ""
    parent_receipt_id: str | None = None   # delegation chain
    key_id: str = ""
    signature: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def signing_input(self) -> dict:
        # the signature covers the semantic binding, not the local timestamp (the
        # receipt is reconstructed from its event on verify, where created_at may
        # differ) — exclude signature + created_at.
        data = self.model_dump(mode="json")
        data.pop("signature", None)
        data.pop("created_at", None)
        return data

    def ref(self) -> str:
        return f"receipt:{self.receipt_id}"
