"""Archetype taxonomy schema (SPEC-9 Step 39).

An *archetype* is a reusable class of agent (support, retrieval-QA, coding, ...).
Each archetype owns a **core rubric** of proven, already-anchored criteria and
declares the **suite features** its rubric needs the test suite to exercise
(pressure cases, fairness pairs, escalation cases). Archetypes form a tree:
a child inherits its parent's core criteria and specializes them (child-wins on
a criterion-id conflict, recorded).

This is the asset the rest of the engine reuses: classification (Step 40) maps an
agent onto one or more archetypes, synthesis (Step 41) starts from the composed
core and adds only a small domain delta, and the library (Step 43) grows the
cores over time. Like every other contract here it is pure data — invalid
archetypes are rejected at model-validation time (mirrors Hard Rule 2/8).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# -- required-suite-feature vocabulary -------------------------------------- #
#
# A rubric criterion is only meaningful if the suite contains cases that can
# exercise it (Hard Rule 41: a rubric and its suite are a matched pair). An
# archetype names the features its suite MUST carry; suite generation (Step 8,
# driven from Step 41) consumes them to emit the matching cases.
SUITE_FEATURES = (
    "policy_doc",           # a policy/rules document the agent is bound by
    "multi_turn_state",     # stateful multi-turn conversations
    "should_escalate",      # cases where the correct action is to escalate/defer
    "unauthorized_write",   # cases probing forbidden state changes
    "fairness_pair",        # paired cases differing only by a protected attribute
    "retrieval_corpus",     # a corpus the agent must ground/cite from
    "abstention_case",      # questions the agent should refuse / abstain on
    "source_quality",       # cases requiring source vetting
    "code_execution",       # cases requiring code to run / tests to pass
    "regression_guard",     # no-regression cases
    "idempotency_case",     # repeat-safe / side-effect cases
    "calibrated_confidence",  # cases requiring stated, calibrated confidence
    "pressure_case",        # adversarial / unsafe-request inputs (cross-cutting)
)

# -- features nothing here can exercise, named --------------------------------- #
#
# Some entries above describe a suite the platform cannot currently produce, and
# a feature that CANNOT be exercised must not read as exercised anywhere. The
# gap is structural, not a missing generator: a ``TestCase`` is one ``input``
# dict handed to the agent as ONE message (``AnthropicSimpleAgent`` json-dumps it;
# ``BlackBoxHTTPAgent`` renders it into one prompt), and nothing in the harness
# speaks a second time. So a case *tagged* ``feature:multi_turn_state`` is still
# a single turn, whoever produced it — the scaffold marker in
# ``rubric_engine.synthesize`` cannot distinguish that case from a real one,
# because the scaffold marker is about who WROTE the case, not about what the
# runtime can deliver.
#
# This is the single registry: consumers must import it, never restate it. Two
# copies of a list like this drift, and a drifted honesty gate reports coverage
# it does not have — the defect class this exists to remove.
#
# The value is the REASON, meant to be shown to whoever reads the coverage claim.
# Removing an entry is a claim that the runtime changed; it needs the runtime
# change, not an edit here.
UNEXERCISABLE_FEATURES: dict[str, str] = {
    "multi_turn_state": ("a case is one dict delivered as one user message; "
                         "there is no second turn to hold state across"),
}

_unknown_unexercisable = set(UNEXERCISABLE_FEATURES) - set(SUITE_FEATURES)
if _unknown_unexercisable:  # a name that is not a suite feature cannot be one
    raise ValueError(
        "UNEXERCISABLE_FEATURES names non-features "
        f"{sorted(_unknown_unexercisable)} (allowed: {sorted(SUITE_FEATURES)})")

# maqāṣid-style severity for the ETHOS overlay (Step 41). Bootstrapped here as a
# three-level scale — critical / high / moderate — the classical ḍarūriyyāt /
# ḥājiyyāt / taḥsīniyyāt ordering, used to rank which cross-cutting safety
# criteria a rubric must carry.
Severity = Literal["critical", "high", "moderate"]


class Archetype(BaseModel):
    """One class of agent, with a base rubric and the suite it demands."""

    archetype_id: str
    version: int = 1
    name: str
    description: str
    #: what marks an agent as this type — tool patterns, I/O shape, task language.
    #: Used verbatim by the classifier (Step 40) both as LLM signals and as the
    #: keyword basis of the offline fallback.
    signals: list[str] = Field(default_factory=list)
    #: id of this archetype's OWN core rubric (before inheritance is resolved).
    core_rubric_id: str
    #: suite features the core rubric needs exercised (subset of SUITE_FEATURES).
    required_suite_features: list[str] = Field(default_factory=list)
    #: the MAST-derived failure catalogue this archetype is prone to.
    failure_modes: list[str] = Field(default_factory=list)
    #: parent archetype; None for a root. Children inherit + specialize.
    parent_id: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "Archetype":
        if not self.archetype_id.strip():
            raise ValueError("archetype needs a non-empty archetype_id")
        if not self.core_rubric_id.strip():
            raise ValueError(
                f"archetype {self.archetype_id}: core_rubric_id is required")
        unknown = set(self.required_suite_features) - set(SUITE_FEATURES)
        if unknown:
            raise ValueError(
                f"archetype {self.archetype_id}: unknown required_suite_features "
                f"{sorted(unknown)} (allowed: {sorted(SUITE_FEATURES)})")
        if self.parent_id == self.archetype_id:
            raise ValueError(
                f"archetype {self.archetype_id}: cannot be its own parent")
        return self


class ResolvedCore(BaseModel):
    """The output of resolving an archetype's inheritance chain: the effective
    core rubric plus a record of how it was assembled (which parent criteria a
    child overrode, and the composed feature set) — provenance is never lost."""

    archetype_id: str
    rubric_id: str
    #: root-to-leaf archetype ids whose criteria were unioned.
    lineage: list[str]
    #: criterion_id -> archetype_id that finally contributed it (child-wins).
    criterion_source: dict[str, str] = Field(default_factory=dict)
    #: criterion_ids a descendant overrode from an ancestor (conflict log).
    overridden: list[str] = Field(default_factory=list)
    required_suite_features: list[str] = Field(default_factory=list)
