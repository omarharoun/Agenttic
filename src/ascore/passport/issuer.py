"""Passport issuance (SPEC-2 T31.3).

Issue/renew/revoke bound to the agent's **latest certification evidence**:

* a passport is short-lived (``passport.ttl_hours``);
* a **revoked or stale** certification cannot carry a live passport — issuance
  refuses (Hard Rule 14/28);
* the **status URL flips on revocation**; verification is checked separately from
  status (a valid signature on a revoked passport is rejected — Hard Rule 28).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from ascore.registry.sqlite_store import NotFoundError
from ascore.schema.passport import Passport, PassportClaims


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PassportIssuer:
    def __init__(self, reg, cfg: dict, key_manager):
        self.reg = reg
        self.cfg = cfg or {}
        self.keys = key_manager

    def _ttl_hours(self) -> float:
        return float((self.cfg.get("passport", {}) or {}).get("ttl_hours", 168))

    def _status_url(self, passport_id: str) -> str:
        base = (self.cfg.get("passport", {}) or {}).get(
            "status_url_base", "https://agenttic.local")
        return f"{base}/passport/{passport_id}/status"

    def issue(self, agent_id: str, *, now: datetime | None = None) -> Passport:
        """Issue a passport bound to the agent's latest certification evidence.
        Refuses if the certification is revoked or stale."""
        from ascore.certification.staleness import status as cert_status

        now = now or _now()
        dossier = self.reg.latest_dossier(agent_id)  # raises NotFoundError
        cstatus = cert_status(self.reg, dossier)
        if cstatus in ("revoked", "stale"):
            raise ValueError(
                f"certification is {cstatus} — cannot issue a live passport "
                f"for {agent_id} (Hard Rule 14/28)")

        policy_hash = ""
        try:
            policy_hash = self.reg.latest_policy(agent_id).content_hash
        except NotFoundError:
            policy_hash = ""

        stage = "internal"
        try:
            from ascore.release.ladder import agent_stage
            stage = agent_stage(self.reg, agent_id)
        except Exception:  # noqa: BLE001
            pass

        autonomy = None
        try:
            card = self.reg.get_card(agent_id)
            fv = card.fields.get("autonomy_control.autonomy_level_and_planning_depth")
            if fv is not None and fv.status == "value_present":
                autonomy = str(fv.value).split(" ", 1)[0]
        except Exception:  # noqa: BLE001
            pass

        passport_id = f"pp-{uuid.uuid4().hex[:12]}"
        claims = PassportClaims(
            agent_id=agent_id, tier=dossier.tier_decision.tier,
            dossier_sha256=dossier.content_sha256 or "",
            policy_hash=policy_hash, stage=stage, autonomy_level=autonomy,
            attestation_mode=dossier.attestation.mode, issued_at=now,
            expires_at=now + timedelta(hours=self._ttl_hours()),
            status_url=self._status_url(passport_id), key_id=self.keys.key_id())
        passport = Passport(passport_id=passport_id, claims=claims)
        passport.signature = self.keys.sign(passport.signing_input())
        self.reg.save_passport(passport)
        return passport

    def revoke(self, passport_id: str, *, reason: str = "") -> None:
        """Revoke a passport (append-only). The status URL flips to revoked; the
        signature stays valid but verification will now reject it."""
        p = self.reg.get_passport(passport_id)  # raises NotFoundError
        self.reg.append_passport_event(passport_id, p.claims.agent_id, "revoked",
                                       reason=reason)

    def verify(self, passport_id: str, *, now: datetime | None = None) -> dict:
        """Verify a persisted passport: signature validity AND status (checked
        separately). ``valid`` requires a good signature, active status, and
        non-expiry."""
        p = self.reg.get_passport(passport_id)
        return verify_passport_object(
            p, self.keys, status=self.reg.passport_status(passport_id), now=now)


def verify_passport_object(passport: Passport, key_manager, *, status: str,
                           now: datetime | None = None) -> dict:
    """Pure verification of a passport object against a key manager + status."""
    from ascore.passport.keys import verify_payload

    now = now or _now()
    kr = key_manager.keyref_for(passport.claims.key_id)
    sig_valid = False
    reason = ""
    if kr is None:
        reason = f"unknown key id {passport.claims.key_id}"
    else:
        sig_valid = verify_payload(kr.public_key_b64, passport.signing_input(),
                                   passport.signature)
        if not sig_valid:
            reason = "signature does not verify"
    expired = passport.claims.is_expired(now)
    if expired:
        reason = reason or "passport expired"
    if status == "revoked":
        reason = "passport revoked"  # revocation beats a valid signature
    valid = sig_valid and not expired and status != "revoked"
    return {"passport_id": passport.passport_id, "signature_valid": sig_valid,
            "status": status, "expired": expired, "valid": valid,
            "reason": reason if not valid else ""}


def passport_status_view(reg, passport_id: str) -> dict:
    """Public status view (the status URL body)."""
    p = reg.get_passport(passport_id)
    status = reg.passport_status(passport_id)
    return {"passport_id": passport_id, "agent_id": p.claims.agent_id,
            "status": status, "expires_at": p.claims.expires_at.isoformat(),
            "tier": p.claims.tier}
