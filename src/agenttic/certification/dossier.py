"""Dossier assembly (SPEC-2 T14.2).

``assemble()`` gathers the evidence a certification produced — scorecard refs,
calibration, elicitation summary, domain coverage, caveats (verbatim from the
profile), an Inspect EvalLog ref, and the attestation — into a
:class:`~agenttic.schema.certification.Dossier`, computes its ``content_sha256``,
chains it to the agent's previous dossier via ``prev_dossier_sha256``, and (when
persisting) writes it + a ``created`` event through the registry.

Hard Rule 9: every number in the dossier resolves to a persisted id; unassessed
domains carry no fabricated numbers.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from agenttic.certification.hashing import compute_dossier_hash
from agenttic.registry.sqlite_store import NotFoundError
from agenttic.schema.certification import Attestation, Dossier


def _prev_hash(reg, agent_id: str) -> str | None:
    """The content hash of this agent's most recent dossier, if any — the chain
    link. None for the first dossier."""
    if reg is None:
        return None
    try:
        prev = reg.latest_dossier(agent_id)
    except NotFoundError:
        return None
    return prev.content_sha256


def assemble(
    reg,
    *,
    agent_id: str,
    agent_config_hash: str,
    profile,
    tier_decision,
    coverage,
    attestation: Attestation,
    scorecard_refs: list[str] | None = None,
    calibration: dict | None = None,
    elicitation: dict | None = None,
    inspect_log_ref: str | None = None,
    dossier_id: str | None = None,
    prev_dossier_sha256: str | None = None,
    persist: bool = True,
) -> Dossier:
    """Build (and optionally persist) a dossier. The caveats are copied verbatim
    from the profile; the hash chain links to the agent's previous dossier."""
    dossier = Dossier(
        dossier_id=dossier_id or f"dossier-{uuid.uuid4().hex[:12]}",
        agent_id=agent_id,
        agent_config_hash=agent_config_hash,
        profile_id=profile.profile_id,
        profile_version=profile.version,
        tier_decision=tier_decision,
        attestation=attestation,
        coverage=list(coverage or []),
        caveats=list(profile.caveats or []),  # verbatim
        scorecard_refs=list(scorecard_refs or []),
        calibration=dict(calibration or {}),
        elicitation=elicitation,
        inspect_log_ref=inspect_log_ref,
        prev_dossier_sha256=(
            prev_dossier_sha256 if prev_dossier_sha256 is not None
            else _prev_hash(reg, agent_id)
        ),
    )
    dossier.content_sha256 = compute_dossier_hash(dossier)
    if persist and reg is not None:
        reg.save_dossier(dossier)  # also appends the 'created' event
    return dossier


# --------------------------------------------------------------------------- #
# Offline verification (T14.3).
# --------------------------------------------------------------------------- #


@dataclass
class VerifyResult:
    ok: bool
    dossier_id: str
    tier: str = ""
    problems: list[str] = field(default_factory=list)  # each names the offending ref

    def raise_for_status(self) -> "VerifyResult":
        if not self.ok:
            raise DossierVerificationError(
                f"dossier {self.dossier_id} failed verification: "
                + "; ".join(self.problems))
        return self


class DossierVerificationError(ValueError):
    """A dossier failed offline verification. The message names the offending
    ref (Hard Rule 27: verification failures are hard, named errors)."""


def verify_dossier(dossier: Dossier, reg=None) -> VerifyResult:
    """Recompute the dossier's content hash offline and check it against the
    stored value. When ``reg`` is given and the dossier chains to a previous
    one, verify the chain link too. Every problem names the offending ref."""
    problems: list[str] = []
    recomputed = compute_dossier_hash(dossier)
    if not dossier.content_sha256:
        problems.append(f"{dossier.ref()}: missing content_sha256")
    elif recomputed != dossier.content_sha256:
        problems.append(
            f"{dossier.ref()}: content hash mismatch "
            f"(stored {dossier.content_sha256[:12]}… != "
            f"recomputed {recomputed[:12]}…)")

    # tier evidence must resolve to at least one persisted id (schema guarantees
    # non-empty; we name it if somehow empty on a hand-built object).
    if not dossier.tier_decision.evidence_refs:
        problems.append(f"{dossier.ref()}: tier decision cites no evidence")

    # chain link (needs the registry to fetch the previous dossier)
    if reg is not None and dossier.prev_dossier_sha256:
        prev = _find_prev(reg, dossier)
        if prev is None:
            problems.append(
                f"{dossier.ref()}: prev_dossier_sha256 "
                f"{dossier.prev_dossier_sha256[:12]}… has no matching dossier")
        elif prev.content_sha256 != dossier.prev_dossier_sha256:
            problems.append(
                f"{dossier.ref()}: broken chain link to {prev.ref()} "
                f"(expected {dossier.prev_dossier_sha256[:12]}…, "
                f"found {prev.content_sha256[:12]}…)")

    return VerifyResult(ok=not problems, dossier_id=dossier.dossier_id,
                        tier=dossier.tier_decision.tier, problems=problems)


def _find_prev(reg, dossier: Dossier) -> Dossier | None:
    for row in reg.list_dossiers(dossier.agent_id):
        if row["content_sha256"] == dossier.prev_dossier_sha256:
            return reg.get_dossier(row["dossier_id"])
    return None


def revoke(reg, dossier_id: str, *, reason: str, actor: str = "",
           cfg: dict | None = None) -> None:
    """Revoke a dossier by appending a ``revoked`` event (append-only). The
    dossier remains readable forever; its computed status flips to ``revoked``
    (Hard Rule 14: status is computed or revoked, never manually granted — there
    is deliberately NO promotion path). A blank reason is rejected."""
    if not (reason or "").strip():
        raise ValueError("revocation requires a reason")
    d = reg.get_dossier(dossier_id)  # raises NotFoundError if absent
    reg.append_dossier_event(dossier_id, d.agent_id, "revoked",
                             reason=reason.strip())
    # revocation is an evidence change → recompile to a serve:deny posture
    try:
        from agenttic.config import load_config
        from agenttic.enforce.compiler import recompile_for_agent
        if cfg is None:
            try:
                cfg = load_config()
            except Exception:  # noqa: BLE001
                cfg = {}
        recompile_for_agent(reg, cfg, d.agent_id)
        from agenttic.feeds.webhooks import REVOCATION, enqueue_webhook
        enqueue_webhook(reg, cfg, REVOCATION, d.agent_id,
                        {"dossier_id": dossier_id, "reason": reason.strip()})
    except Exception:  # noqa: BLE001 — enforcement optional
        pass


def verify(target, reg=None) -> VerifyResult:
    """Verify a dossier given a filesystem path (offline, JSON alone), or a
    dossier id resolved against ``reg``."""
    p = Path(str(target))
    if reg is not None and not p.exists():
        dossier = reg.get_dossier(str(target))
        return verify_dossier(dossier, reg)
    dossier = Dossier.model_validate(json.loads(p.read_text()))
    return verify_dossier(dossier, reg)
