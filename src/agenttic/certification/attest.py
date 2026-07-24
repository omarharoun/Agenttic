"""Attestation — sign the evidence, never the verdict (SPEC-12 Step 54).

Two tiers, matching the open-core line (Hard Rule 55 — the tier is always stated
in the artifact, and local self-attestation is never presented as third-party
assurance):

* **local self-attestation** (OSS, free, offline): an Ed25519 keypair generated
  on first use and kept in the user's config dir. Proves *integrity* — that
  nothing was altered since it was measured — NOT neutrality.
* **assurance** (commercial): signed with the platform's published issuer key
  (``safety_cert.signing_key``, fail-closed in production), optionally anchored
  in a transparency log so a third party verifies without trusting us.

Verification recomputes every hash from the stored evidence and reports a precise
reason for any failure. Certificates expire (Hard Rule 52), are bound to an exact
``agent_config_hash`` (Hard Rule 53), and drift suspends them automatically.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)

from agenttic.schema.attestation import (
    BANNED_CLAIMS, EvidenceManifest, ManifestStatus, RevocationEntry,
    RevocationList, SignedManifest, content_hash)

DEFAULT_EXPIRY_DAYS = 90          # mirrors safety_cert.DEFAULT_EXPIRY_DAYS
#: Test/CI override for where the local key lives.
LOCAL_KEY_DIR_ENV = "AGENTTIC_ATTEST_KEY_DIR"


# --------------------------------------------------------------------------- #
# local self-attestation key (OSS tier) — generated on first use
# --------------------------------------------------------------------------- #

def local_key_dir() -> Path:
    override = os.environ.get(LOCAL_KEY_DIR_ENV)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "agenttic"


def local_key_path() -> Path:
    return local_key_dir() / "attest-key"


def local_signing_key() -> Ed25519PrivateKey:
    """Load the user's local attestation key, generating it on first use.
    Written 0600 — it is a private key, never printed or committed."""
    path = local_key_path()
    if path.exists():
        seed = base64.b64decode(path.read_text(encoding="utf-8").strip())
        if len(seed) != 32:
            raise ValueError(f"local attest key at {path} is not a 32-byte seed")
        return Ed25519PrivateKey.from_private_bytes(seed)
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes_raw()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(base64.b64encode(seed).decode(), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def key_id(public: Ed25519PublicKey) -> str:
    """Public key id. Same construction as the certification key ids
    (``ed25519:`` + first 16 hex of sha256 over the raw public bytes)."""
    from agenttic.certification.safety_cert import key_id as _kid
    return _kid(public)


def public_key_b64(public: Ed25519PublicKey) -> str:
    from cryptography.hazmat.primitives import serialization
    raw = public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def _signing_key_for(tier: str, cfg: dict | None):
    """(private_key, issuer_label) for a tier."""
    if tier == "assurance":
        from agenttic.certification.safety_cert import signing_key
        return signing_key(cfg), "agenttic-assurance"
    return local_signing_key(), "local-self-attested"


# --------------------------------------------------------------------------- #
# build + sign
# --------------------------------------------------------------------------- #

DEFAULT_SCOPE = (
    "Attests what was measured: suite {suite}@v{sv} scored under rubric "
    "{rubric}@v{rv} at k={k}, against agent config {cfg}.")
DEFAULT_LIMITS = (
    "Attests only the listed evidence under the stated conditions. Domains not "
    "listed are NOT ASSESSED. This is not a guarantee of future behaviour, and "
    "it makes no claim beyond the measurements recorded here.")


def build_manifest(
    *,
    manifest_id: str,
    agent_id: str,
    agent_config_hash: str,
    suite_id: str,
    suite_version: int,
    rubric_id: str,
    rubric_version: int,
    scorecard: dict,
    visibility_tier: str = "glass_box",
    k: int = 1,
    judge_configs: list | None = None,
    integrity_gates: dict | None = None,
    contamination: dict | None = None,
    environment: dict | None = None,
    abom_sha256: str | None = None,
    signoff_sha256: str | None = None,
    user_source: str = "simulated",
    issuer: str = "local-self-attested",
    signing_tier: str = "local_self_attested",
    issued_at: datetime | None = None,
    expires_in_days: int = DEFAULT_EXPIRY_DAYS,
    scope_statement: str | None = None,
    limits_statement: str | None = None,
) -> EvidenceManifest:
    """Assemble a manifest from a scorecard. The scorecard is hashed, never
    inlined — verification recomputes the hash from the stored scorecard."""
    issued = issued_at or datetime.now(timezone.utc)
    scope = scope_statement or DEFAULT_SCOPE.format(
        suite=suite_id, sv=suite_version, rubric=rubric_id, rv=rubric_version,
        k=k, cfg=agent_config_hash[:12])
    return EvidenceManifest(
        manifest_id=manifest_id,
        subject={"agent_id": agent_id, "agent_config_hash": agent_config_hash},
        suite_id=suite_id, suite_version=suite_version,
        rubric_id=rubric_id, rubric_version=rubric_version,
        judge_configs=judge_configs or [],
        k=k,
        integrity_gates=integrity_gates or {},
        contamination=contamination or {},
        scorecard_hash=content_hash(scorecard),
        visibility_tier=visibility_tier,           # type: ignore[arg-type]
        user_source=user_source,                    # type: ignore[arg-type]
        environment=environment or {},
        abom_sha256=abom_sha256,
        signoff_sha256=signoff_sha256,
        issued_at=issued,
        expires_at=issued + timedelta(days=expires_in_days),
        issuer=issuer,
        signing_tier=signing_tier,                  # type: ignore[arg-type]
        scope_statement=scope,
        limits_statement=limits_statement or DEFAULT_LIMITS,
    )


def sign_manifest(manifest: EvidenceManifest, *, cfg: dict | None = None,
                  transparency_log_url: str | None = None) -> SignedManifest:
    """Sign the manifest hash with the key for its declared tier."""
    key, _issuer = _signing_key_for(manifest.signing_tier, cfg)
    digest = manifest.manifest_hash()
    sig = key.sign(digest.encode("utf-8"))
    return SignedManifest(
        manifest=manifest,
        manifest_sha256=digest,
        signature=base64.b64encode(sig).decode(),
        kid=key_id(key.public_key()),
        transparency_log_url=transparency_log_url,
    )


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #

@dataclass
class VerifyResult:
    ok: bool
    status: ManifestStatus
    manifest_id: str
    problems: list[str] = field(default_factory=list)
    reason: str = ""

    def raise_for_status(self) -> "VerifyResult":
        if not self.ok:
            raise ValueError(
                f"manifest {self.manifest_id} is {self.status}: "
                + "; ".join(self.problems))
        return self


def verify_manifest(
    signed: SignedManifest,
    *,
    public_key_b64_str: str | None = None,
    scorecard: dict | None = None,
    abom: dict | None = None,
    current_config_hash: str | None = None,
    revocations: RevocationList | None = None,
    now: datetime | None = None,
) -> VerifyResult:
    """Verify a signed manifest, recomputing every hash from stored evidence.

    Reports a PRECISE reason: an altered scorecard, a changed rubric version, a
    subject whose config hash no longer matches, expiry, or revocation.
    Revocation and expiry are reported as their own statuses (not "invalid"), so
    a relying party can tell "this was tampered with" from "this lapsed"."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    m = signed.manifest
    problems: list[str] = []

    # 1. the manifest body must hash to the value that was signed
    recomputed = m.manifest_hash()
    if recomputed != signed.manifest_sha256:
        problems.append(
            f"manifest body altered: recomputed hash {recomputed[:16]}… != "
            f"signed {signed.manifest_sha256[:16]}… (a field such as the rubric "
            "version or k was changed after signing)")

    # 2. the signature must verify over that hash
    if public_key_b64_str:
        try:
            pub = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(public_key_b64_str))
            pub.verify(base64.b64decode(signed.signature),
                       signed.manifest_sha256.encode("utf-8"))
        except Exception:
            problems.append("signature does not verify against the supplied public key")

    # 3. the scorecard must still hash to what the manifest recorded
    if scorecard is not None:
        actual = content_hash(scorecard)
        if actual != m.scorecard_hash:
            problems.append(
                f"scorecard altered: recomputed {actual[:16]}… != attested "
                f"{m.scorecard_hash[:16]}…")

    # 4. the ABOM must match its referenced hash
    if abom is not None and m.abom_sha256:
        actual = content_hash(abom)
        if actual != m.abom_sha256:
            problems.append(
                f"ABOM altered: recomputed {actual[:16]}… != referenced "
                f"{m.abom_sha256[:16]}…")

    # 5. the subject must still be the agent that was measured (Hard Rule 53)
    if current_config_hash is not None and current_config_hash != m.subject.agent_config_hash:
        problems.append(
            f"subject mismatch: certificate is bound to agent_config_hash "
            f"{m.subject.agent_config_hash[:12]}… but the deployed agent is "
            f"{current_config_hash[:12]}… — the agent changed, so this "
            "certificate does not describe it")

    if problems:
        return VerifyResult(False, "invalid", m.manifest_id, problems,
                            reason=problems[0])

    # 6. revocation beats expiry beats validity
    if revocations is not None:
        entry = revocations.status_for(m.manifest_id)
        if entry is not None:
            return VerifyResult(
                False, entry.status, m.manifest_id,
                [f"{entry.status}: {entry.reason} (source: {entry.source})"],
                reason=entry.reason)

    if m.is_expired(now):
        return VerifyResult(
            False, "expired", m.manifest_id,
            [f"expired at {m.expires_at.isoformat()}"],
            reason="certificate expired — agents drift, so certificates lapse")

    return VerifyResult(True, "valid", m.manifest_id, [],
                        reason="signature, evidence hashes and subject all match")


# --------------------------------------------------------------------------- #
# revocation — expiry's active twin (Hard Rule 52)
# --------------------------------------------------------------------------- #

def new_revocation_list(issuer: str = "agenttic") -> RevocationList:
    return RevocationList(issuer=issuer, updated_at=datetime.now(timezone.utc))


def append_revocation(
    rl: RevocationList, *, manifest_id: str, subject_config_hash: str,
    status: str = "suspended", reason: str, source: str = "manual",
    now: datetime | None = None,
) -> RevocationEntry:
    """Append-only: entries are never edited or removed, so the list is an
    auditable history rather than mutable state."""
    entry = RevocationEntry(
        manifest_id=manifest_id, subject_config_hash=subject_config_hash,
        status=status,                                # type: ignore[arg-type]
        reason=reason, source=source,
        recorded_at=now or datetime.now(timezone.utc))
    rl.entries.append(entry)
    rl.updated_at = entry.recorded_at
    return entry


def suspend_on_drift(
    rl: RevocationList, manifests: list[EvidenceManifest], *,
    reeval_reasons_for: dict[str, list[str]], now: datetime | None = None,
) -> list[RevocationEntry]:
    """Drift revokes automatically. Given the live monitor's re-eval requests
    (``reg.reeval_requests(agent_id)``), suspend every active manifest for that
    subject and record why.

    ``reeval_reasons_for`` maps agent_id -> reasons filed by
    :meth:`agenttic.live.monitor.LiveMonitor.status`."""
    out: list[RevocationEntry] = []
    for m in manifests:
        reasons = reeval_reasons_for.get(m.subject.agent_id) or []
        if not reasons:
            continue
        if rl.status_for(m.manifest_id) is not None:
            continue                                   # already suspended/revoked
        out.append(append_revocation(
            rl, manifest_id=m.manifest_id,
            subject_config_hash=m.subject.agent_config_hash,
            status="suspended",
            reason="live drift detected: " + "; ".join(reasons[:3]),
            source="drift:re_eval_request", now=now))
    return out


def sign_revocation_list(rl: RevocationList, *, cfg: dict | None = None,
                         tier: str = "assurance") -> dict:
    """Sign the published revocation list so relying parties can trust it."""
    key, _ = _signing_key_for(tier, cfg)
    digest = rl.content_sha256()
    sig = key.sign(digest.encode("utf-8"))
    return {
        "revocation_list": rl.model_dump(mode="json"),
        "content_sha256": digest,
        "signature": base64.b64encode(sig).decode(),
        "kid": key_id(key.public_key()),
        "algorithm": "ed25519",
    }


# --------------------------------------------------------------------------- #
# honesty guard (Hard Rule 51)
# --------------------------------------------------------------------------- #

def assert_no_banned_claims(text: str, *, where: str = "artifact") -> None:
    """Raise if a rendered artifact makes an unbounded safety claim."""
    low = text.lower()
    for claim in BANNED_CLAIMS:
        if claim in low:
            raise AssertionError(
                f"{where} asserts a banned unbounded claim {claim!r} — sign the "
                "evidence, never the verdict (Hard Rule 51)")


def render_certificate(signed: SignedManifest, result: VerifyResult | None = None) -> str:
    """Human-readable certificate. States scope, limits, and the signing TIER —
    a local self-attestation is never presented as third-party assurance."""
    m = signed.manifest
    tier_line = (
        "LOCAL SELF-ATTESTATION — proves integrity (nothing was altered since "
        "measurement). This is NOT a third-party assurance: the issuer ran the "
        "evaluation themselves."
        if m.signing_tier == "local_self_attested" else
        "ASSURANCE ATTESTATION — issued by Agenttic as a neutral third party.")
    lines = [
        "AGENTTIC EVIDENCE ATTESTATION",
        "=" * 60,
        f"manifest        {m.manifest_id}",
        f"subject         {m.subject.agent_id} @ config {m.subject.agent_config_hash[:16]}…",
        f"measured        suite {m.suite_id}@v{m.suite_version} · rubric "
        f"{m.rubric_id}@v{m.rubric_version} · k={m.k} · {m.visibility_tier}",
        f"scorecard       sha256 {m.scorecard_hash[:32]}…",
        f"abom            {('sha256 ' + m.abom_sha256[:32] + '…') if m.abom_sha256 else '(none)'}",
        f"issued          {m.issued_at.isoformat()}  by {m.issuer}",
        f"expires         {m.expires_at.isoformat()}",
        f"signature       {m.signing_tier} · {signed.kid}",
        "",
        f"TIER            {tier_line}",
        "",
        f"SCOPE           {m.scope_statement}",
        f"LIMITS          {m.limits_statement}",
    ]
    if result is not None:
        lines += ["", f"STATUS          {result.status.upper()} — {result.reason}"]
    text = "\n".join(lines)
    assert_no_banned_claims(text, where="rendered certificate")
    return text
