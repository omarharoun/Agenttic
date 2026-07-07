"""Agent card schema (SPEC-2 M9, T19.1).

An agent card is a structured, provenance-tracked description of an agent, modeled
on the AI Agent Index field taxonomy. The core honesty invariants (Hard Rules
15/16):

* **Provenance is computed from refs, never asserted.** A ``measured`` value needs
  evidence refs (traces/scorecards/dossiers); a ``documented`` value needs
  citations; an ``attested`` value needs a tenant signature. **No refs ⇒ no
  value** — you cannot present a value with nothing backing it.
* **``none_found`` ≠ ``confirmed_none``.** "We didn't find it" is free;
  *confirming* absence requires evidence (a citation or measurement). Silent
  upgrades between these are impossible.

Cards are append-only versioned; ``source`` records whether Agenttic produced the
card from its own data (``agenttic``) or imported it from the Index
(``index_import``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

FieldStatus = Literal["value_present", "none_found", "confirmed_none", "not_applicable"]
Provenance = Literal["measured", "documented", "attested"]
CardSource = Literal["agenttic", "index_import"]


class FieldValue(BaseModel):
    """One card field with its status + provenance-backing refs."""

    field_key: str
    status: FieldStatus
    value: Any | None = None
    provenance: Provenance | None = None
    evidence_refs: list[str] = Field(default_factory=list)  # measured backing
    citations: list[str] = Field(default_factory=list)      # documented backing
    signature: str | None = None                            # attested backing

    @model_validator(mode="after")
    def _provenance_from_refs(self) -> "FieldValue":
        has_measured = bool(self.evidence_refs)
        has_documented = bool(self.citations)
        has_attested = bool(self.signature)
        has_any_backing = has_measured or has_documented or has_attested

        if self.status == "value_present":
            if self.value is None:
                raise ValueError(
                    f"{self.field_key}: status value_present requires a value")
            if self.provenance is None:
                raise ValueError(
                    f"{self.field_key}: a present value must declare provenance")
            # provenance is computed from refs — the declared class must have its
            # backing, and there must be SOME backing (no refs ⇒ no value).
            if self.provenance == "measured" and not has_measured:
                raise ValueError(
                    f"{self.field_key}: measured provenance requires evidence_refs")
            if self.provenance == "documented" and not has_documented:
                raise ValueError(
                    f"{self.field_key}: documented provenance requires citations")
            if self.provenance == "attested" and not has_attested:
                raise ValueError(
                    f"{self.field_key}: attested provenance requires a signature")
            if not has_any_backing:
                raise ValueError(
                    f"{self.field_key}: no refs ⇒ no value (Hard Rule 15)")

        elif self.status == "confirmed_none":
            # confirming absence requires evidence — otherwise it is only none_found
            if not (has_measured or has_documented):
                raise ValueError(
                    f"{self.field_key}: confirmed_none requires a citation or "
                    f"measurement (none_found ≠ confirmed_none, Hard Rule 16)")

        else:  # none_found / not_applicable carry no value
            if self.value is not None:
                raise ValueError(
                    f"{self.field_key}: status {self.status} must not carry a value")
            if self.provenance is not None:
                raise ValueError(
                    f"{self.field_key}: status {self.status} has no provenance")
        return self

    @staticmethod
    def none_found(field_key: str) -> "FieldValue":
        return FieldValue(field_key=field_key, status="none_found")

    @staticmethod
    def measured(field_key: str, value: Any, evidence_refs: list[str]) -> "FieldValue":
        return FieldValue(field_key=field_key, status="value_present", value=value,
                          provenance="measured", evidence_refs=list(evidence_refs))

    @staticmethod
    def documented(field_key: str, value: Any, citations: list[str]) -> "FieldValue":
        return FieldValue(field_key=field_key, status="value_present", value=value,
                          provenance="documented", citations=list(citations))

    @staticmethod
    def attested(field_key: str, value: Any, signature: str) -> "FieldValue":
        return FieldValue(field_key=field_key, status="value_present", value=value,
                          provenance="attested", signature=signature)


class AgentCard(BaseModel):
    """A versioned, append-only agent card. ``fields`` is keyed by field_key."""

    agent_id: str
    version: int = 1
    source: CardSource = "agenttic"
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    attribution: str | None = None  # set for index_import (CC BY)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get(self, field_key: str) -> FieldValue | None:
        return self.fields.get(field_key)

    def present_fields(self) -> dict[str, FieldValue]:
        return {k: v for k, v in self.fields.items() if v.status == "value_present"}

    def ref(self) -> str:
        return f"card:{self.agent_id}@v{self.version}"
