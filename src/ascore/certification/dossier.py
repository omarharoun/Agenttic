"""Dossier assembly (SPEC-2 T14.2).

``assemble()`` gathers the evidence a certification produced — scorecard refs,
calibration, elicitation summary, domain coverage, caveats (verbatim from the
profile), an Inspect EvalLog ref, and the attestation — into a
:class:`~ascore.schema.certification.Dossier`, computes its ``content_sha256``,
chains it to the agent's previous dossier via ``prev_dossier_sha256``, and (when
persisting) writes it + a ``created`` event through the registry.

Hard Rule 9: every number in the dossier resolves to a persisted id; unassessed
domains carry no fabricated numbers.
"""

from __future__ import annotations

import uuid

from ascore.certification.hashing import compute_dossier_hash
from ascore.registry.sqlite_store import NotFoundError
from ascore.schema.certification import Attestation, Dossier


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
