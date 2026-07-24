"""Assertion sets — the versioned artifact that pins WHICH properties a run
monitored (SPEC-13 Step 62).

The assertion *implementations* live in code (a registry, like `@check`), but the
*set* a run used is a versioned registry artifact, not a code constant: otherwise
"assertions clean" is unfalsifiable, because nobody can say which properties were
in force. Pinning the set makes an assertion suite a diff a human approves —
quietly dropping a property is then visible as a version bump.

Severity may be overridden per set (an irreversible-action property can be
critical for a payments agent and standard for a read-only one) without editing
the shipped library.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, model_validator


class AssertionSet(BaseModel):
    """A pinned, versioned collection of assertion ids."""

    set_id: str
    version: int = 1
    description: str = ""
    assertion_ids: list[str] = Field(default_factory=list)
    #: assertion_id -> severity override ("critical" | "high" | "standard")
    severity_overrides: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _validate(self) -> "AssertionSet":
        if not self.set_id.strip():
            raise ValueError("assertion set needs a non-empty set_id")
        if not self.assertion_ids:
            raise ValueError(
                f"assertion set {self.set_id}: must pin at least one assertion — "
                "an empty set would report 'assertions clean' having checked nothing")
        dupes = {a for a in self.assertion_ids
                 if self.assertion_ids.count(a) > 1}
        if dupes:
            raise ValueError(
                f"assertion set {self.set_id}: duplicate assertion ids {sorted(dupes)}")
        unknown_sev = {k: v for k, v in self.severity_overrides.items()
                       if v not in ("critical", "high", "standard")}
        if unknown_sev:
            raise ValueError(
                f"assertion set {self.set_id}: invalid severity overrides {unknown_sev}")
        stray = set(self.severity_overrides) - set(self.assertion_ids)
        if stray:
            raise ValueError(
                f"assertion set {self.set_id}: severity overrides for assertions "
                f"not in the set: {sorted(stray)}")
        return self

    def ref(self) -> str:
        return f"assertions:{self.set_id}@v{self.version}"

    def validate_against_registry(self) -> None:
        """Fail loudly if the set pins an assertion that is not registered —
        never defer this to evaluation time (mirrors validate_rubric_checks)."""
        from agenttic.verification.assertions import ASSERTIONS
        missing = [a for a in self.assertion_ids if a not in ASSERTIONS]
        if missing:
            raise ValueError(
                f"assertion set {self.set_id} v{self.version} references "
                f"unregistered assertion(s): {sorted(missing)}")


def default_assertion_set(set_id: str = "builtin-default",
                          version: int = 1) -> AssertionSet:
    """The shipped library as a pinned set — the starting point an operator
    versions from."""
    from agenttic.verification.builtins import DEFAULT_ASSERTION_IDS
    return AssertionSet(
        set_id=set_id, version=version,
        description="The built-in assertion library shipped with Agenttic.",
        assertion_ids=list(DEFAULT_ASSERTION_IDS))
