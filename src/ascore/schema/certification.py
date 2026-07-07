"""Certification schema (SPEC-2 M4).

The certification track turns a completed evaluation into an evidence *dossier*
that a third party can verify offline. Everything here is a pure data contract:

* :class:`CertificationProfile` — a *pinned* recipe (suite versions + thresholds
  keyed to the metric catalog) that defines what "certified against X" means.
* :class:`TierDecision` — the A/B/C outcome. Every tier decision MUST cite
  evidence (``evidence_refs`` non-empty) or it is invalid (Hard Rule 9).
* :class:`Attestation` — self-attested vs independent; *computed* from tenancy,
  never selected (Hard Rule 13).
* :class:`DomainCoverage` — the honesty stance per capability domain:
  ``assessed_real`` / ``assessed_seed`` / ``not_assessed`` (Hard Rule 9).
* :class:`Dossier` — the signed-later, hash-chained evidence bundle. Its own
  ``content_sha256`` is excluded from the hash it names; ``prev_dossier_sha256``
  chains dossiers for an agent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# -- vocabularies ----------------------------------------------------------- #

Tier = Literal["A", "B", "C"]
CoverageStatus = Literal["assessed_real", "assessed_seed", "not_assessed"]
AttestationMode = Literal["self_attested", "independent"]

# The eight capability domains a certification profile can require.
CAPABILITY_DOMAINS = (
    "tool_use",
    "reliability",
    "faithfulness",
    "harm_refusal",
    "injection_robustness",
    "autonomy_proxy",
    "deception_probe",
    "cbrn_proxy",
)


class SuiteRef(BaseModel):
    """A pinned reference to an exact suite version. Certification is only
    reproducible when the suite version is fixed, never floating."""

    suite_id: str
    version: int

    def ref(self) -> str:
        return f"suite:{self.suite_id}@v{self.version}"


class CertificationProfile(BaseModel):
    """A pinned certification recipe. Thresholds are keyed to metric-catalog ids;
    the profile loader (``certification/profiles.py``) validates the keys and the
    suite refs against the registry and fails loudly on anything unknown."""

    profile_id: str
    version: int = 1
    description: str = ""
    suite_refs: list[SuiteRef] = Field(default_factory=list)
    required_domains: list[str] = Field(default_factory=list)
    min_k: int = 1
    thresholds: dict[str, float] = Field(default_factory=dict)
    floors: dict[str, float] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _known_domains(self) -> "CertificationProfile":
        unknown = [d for d in self.required_domains if d not in CAPABILITY_DOMAINS]
        if unknown:
            raise ValueError(
                f"profile {self.profile_id}: unknown capability domains {unknown}; "
                f"must be a subset of {list(CAPABILITY_DOMAINS)}"
            )
        return self

    def ref(self) -> str:
        return f"profile:{self.profile_id}@v{self.version}"


class TierDecision(BaseModel):
    """The A/B/C certification outcome. Invalid unless it cites evidence."""

    tier: Tier
    evidence_refs: list[str]
    caps_applied: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _evidence_required(self) -> "TierDecision":
        if not self.evidence_refs:
            raise ValueError(
                "TierDecision.evidence_refs must be non-empty — a tier with no "
                "evidence is invalid (Hard Rule 9)"
            )
        return self


class Attestation(BaseModel):
    """Who stands behind the dossier. ``mode`` is computed from tenancy at
    assembly time (owner => self_attested, evaluator => independent); it is never
    a user-selectable field (Hard Rule 13)."""

    mode: AttestationMode
    tenant: str
    attested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class DomainCoverage(BaseModel):
    """Per-domain honesty stance. ``not_assessed`` is never papered over as a
    number; ``assessed_seed`` (placeholder/seed data) is never promoted to
    ``assessed_real`` without a real ingest record (Hard Rule 9)."""

    domain: str
    status: CoverageStatus
    evidence_refs: list[str] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def _known_domain(self) -> "DomainCoverage":
        if self.domain not in CAPABILITY_DOMAINS:
            raise ValueError(f"unknown capability domain {self.domain!r}")
        return self


class Dossier(BaseModel):
    """The evidence bundle. ``content_sha256`` is computed over every field
    *except itself* (``certification.hashing.compute_dossier_hash``);
    ``prev_dossier_sha256`` chains an agent's dossiers so renewals are auditable.

    Hard Rule 9: every number in ``scorecard_refs`` / ``calibration`` /
    ``elicitation`` resolves to a persisted id. ``not_assessed`` domains carry no
    fabricated numbers.
    """

    dossier_id: str
    agent_id: str
    agent_config_hash: str
    profile_id: str
    profile_version: int
    tier_decision: TierDecision
    attestation: Attestation
    coverage: list[DomainCoverage] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    scorecard_refs: list[str] = Field(default_factory=list)
    calibration: dict = Field(default_factory=dict)
    elicitation: dict | None = None
    inspect_log_ref: str | None = None
    methodology_version: str = "agenttic-cert/v2"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    prev_dossier_sha256: str | None = None
    content_sha256: str | None = None

    def hashable_content(self) -> dict:
        """The dossier content that the hash covers — everything but
        ``content_sha256`` itself (which would be self-referential)."""
        data = self.model_dump(mode="json")
        data.pop("content_sha256", None)
        return data

    def ref(self) -> str:
        return f"dossier:{self.dossier_id}"
