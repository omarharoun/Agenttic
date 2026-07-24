"""The coverage model — defining what "tested" means (SPEC-13 Step 59).

A coverage model declares the space of situations an agent must be exercised in.
Closure over that model, not pass rate, is the headline (Hard Rule 56): "86%
passed" answers *what passed*; a coverage model answers ***what did we never
exercise?***

Three structural rules are enforced here rather than left to review, because each
is a way this build fails silently:

* **Bins are exhaustive.** Every coverpoint must carry an explicit ``other`` bin.
  A rising ``other`` count is itself a finding — the model is missing a dimension.
* **Deterministic coverpoints cannot be classifier-backed** (anti-pattern §7.5).
  Trajectory shape, tool condition, session shape and data condition are
  deterministic *by construction*; letting them take a classifier because
  predicates are more work would quietly make the whole model provisional.
* **Waiving a bin requires a named reason** (Hard Rule 61). Silent holes are
  forbidden; an unhit bin is always reported.

Classifier-backed bins (``intent``, ``emotional_register``) inherit the SPEC-3
calibration discipline: they are PROVISIONAL until measured against humans.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

#: the catch-all bin every coverpoint must declare
OTHER_BIN = "other"

CoverpointKind = Literal["deterministic", "classifier"]

#: Coverpoints that are deterministic by construction — extracted from spans, so
#: a classifier here is always a mistake (anti-pattern §7.5).
DETERMINISTIC_BY_CONSTRUCTION = frozenset({
    "trajectory", "tool_condition", "session_shape", "data_condition",
})


class Classifier(BaseModel):
    """An anchored judge prompt backing a semantic bin. Subject to the same
    calibration discipline as any judge criterion: PROVISIONAL until measured."""

    prompt: str
    anchors: dict = Field(default_factory=dict)   # must carry pass/fail examples
    calibrated: bool = False                      # Hard Rule 6 / SPEC-3
    alpha: float | None = None                    # agreement with humans, once measured

    @model_validator(mode="after")
    def _anchored(self) -> "Classifier":
        missing = {"pass", "fail"} - set(self.anchors)
        if missing:
            raise ValueError(
                f"classifier bins require pass/fail anchors; missing {sorted(missing)}")
        if self.calibrated and self.alpha is None:
            raise ValueError(
                "a classifier marked calibrated must record the measured alpha")
        return self

    @property
    def provisional(self) -> bool:
        return not self.calibrated


class Bin(BaseModel):
    """One value a coverpoint can take."""

    bin_id: str
    label: str = ""
    #: a registered deterministic predicate (see coverage.extractors)
    predicate_ref: str | None = None
    #: OR an anchored classifier. Never both.
    classifier: Classifier | None = None
    #: hitting an illegal bin is a FAILURE, never coverage
    illegal: bool = False
    #: excluded from closure — requires a named reason (Hard Rule 61)
    waived: bool = False
    reason: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "Bin":
        if self.bin_id == OTHER_BIN:
            if self.predicate_ref or self.classifier:
                raise ValueError(
                    "the 'other' bin is the catch-all — it must declare neither a "
                    "predicate nor a classifier (it is hit when nothing else is)")
        else:
            if bool(self.predicate_ref) == bool(self.classifier):
                raise ValueError(
                    f"bin {self.bin_id}: declare exactly one of predicate_ref or "
                    "classifier")
        if self.waived and not self.reason.strip():
            raise ValueError(
                f"bin {self.bin_id}: waiving a bin requires a named reason "
                "(Hard Rule 61 — silent holes are forbidden)")
        if self.waived and self.illegal:
            raise ValueError(
                f"bin {self.bin_id}: a bin cannot be both illegal and waived — "
                "an illegal bin must never be hit, a waived one merely need not be")
        return self

    @property
    def provisional(self) -> bool:
        return self.classifier is not None and self.classifier.provisional


class Coverpoint(BaseModel):
    """One dimension of the space, with exhaustive bins."""

    coverpoint_id: str
    description: str = ""
    kind: CoverpointKind = "deterministic"
    bins: list[Bin]
    required: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "Coverpoint":
        ids = [b.bin_id for b in self.bins]
        if len(ids) != len(set(ids)):
            raise ValueError(f"coverpoint {self.coverpoint_id}: duplicate bin ids")
        if OTHER_BIN not in ids:
            raise ValueError(
                f"coverpoint {self.coverpoint_id}: bins must be exhaustive — an "
                f"explicit '{OTHER_BIN}' bin is mandatory so an unmodelled "
                "situation is visible instead of silently uncounted")
        if len(ids) < 2:
            raise ValueError(
                f"coverpoint {self.coverpoint_id}: needs at least one real bin "
                "besides 'other'")
        if self.kind == "deterministic":
            classy = [b.bin_id for b in self.bins if b.classifier is not None]
            if classy:
                raise ValueError(
                    f"coverpoint {self.coverpoint_id} is deterministic but bins "
                    f"{classy} are classifier-backed — deterministic coverpoints "
                    "are extracted from spans by construction (anti-pattern §7.5)")
        if (self.coverpoint_id in DETERMINISTIC_BY_CONSTRUCTION
                and self.kind != "deterministic"):
            raise ValueError(
                f"coverpoint {self.coverpoint_id} is deterministic by "
                "construction and may not be declared classifier-backed")
        return self

    @property
    def provisional(self) -> bool:
        """True if ANY bin is classifier-backed and not yet calibrated — the
        whole coverpoint's numbers render PROVISIONAL."""
        return any(b.provisional for b in self.bins)

    def bin(self, bin_id: str) -> Bin | None:
        return next((b for b in self.bins if b.bin_id == bin_id), None)

    def countable_bins(self) -> list[Bin]:
        """Bins that count toward closure: not illegal, not waived, not `other`.
        `other` is measured but is a finding, never a coverage target."""
        return [b for b in self.bins
                if not b.illegal and not b.waived and b.bin_id != OTHER_BIN]


class Cross(BaseModel):
    """A combination of coverpoints. Crosses are where the value lives: testing
    'angry customers' and 'refunds' separately proves nothing about angry
    customers demanding out-of-policy refunds during a tool outage."""

    cross_id: str
    coverpoints: list[str]
    #: combinations that must never be generated or counted, as {cp_id: bin_id}
    illegal_combinations: list[dict[str, str]] = Field(default_factory=list)
    #: "all" = the full legal product; or an explicit list of target combinations
    target: object = "all"

    @model_validator(mode="after")
    def _validate(self) -> "Cross":
        if len(self.coverpoints) < 2:
            raise ValueError(f"cross {self.cross_id}: needs at least two coverpoints")
        if len(set(self.coverpoints)) != len(self.coverpoints):
            raise ValueError(f"cross {self.cross_id}: duplicate coverpoints")
        if self.target != "all" and not isinstance(self.target, list):
            raise ValueError(
                f"cross {self.cross_id}: target must be 'all' or a list of "
                "combinations")
        return self


class CoverageModel(BaseModel):
    """A versioned, archetype-scoped declaration of what 'tested' means."""

    model_id: str
    version: int = 1
    archetype_id: str = ""
    coverpoints: list[Coverpoint]
    crosses: list[Cross] = Field(default_factory=list)
    closure_target: float = 0.95

    @model_validator(mode="after")
    def _validate(self) -> "CoverageModel":
        ids = [c.coverpoint_id for c in self.coverpoints]
        if len(ids) != len(set(ids)):
            raise ValueError(f"model {self.model_id}: duplicate coverpoint ids")
        if not (0.0 < self.closure_target <= 1.0):
            raise ValueError(
                f"model {self.model_id}: closure_target must be in (0, 1]")
        known = set(ids)
        for x in self.crosses:
            unknown = [c for c in x.coverpoints if c not in known]
            if unknown:
                raise ValueError(
                    f"cross {x.cross_id}: unknown coverpoints {unknown}")
            for combo in x.illegal_combinations:
                bad = {k: v for k, v in combo.items()
                       if k not in known
                       or self.coverpoint(k).bin(v) is None}  # type: ignore[union-attr]
                if bad:
                    raise ValueError(
                        f"cross {x.cross_id}: illegal_combination references "
                        f"unknown coverpoint/bin {bad}")
        return self

    def coverpoint(self, cp_id: str) -> Coverpoint | None:
        return next((c for c in self.coverpoints if c.coverpoint_id == cp_id), None)

    @property
    def provisional_coverpoints(self) -> list[str]:
        """Coverpoints whose numbers must render PROVISIONAL (uncalibrated
        classifier bins)."""
        return [c.coverpoint_id for c in self.coverpoints if c.provisional]

    def bins_fingerprint(self) -> str:
        """A hash over every bin definition. Bins are versioned artifacts:
        widening or deleting a bin to hit the closure target (anti-pattern §7.7)
        changes this fingerprint, so it is a diff a human approves rather than a
        silent edit."""
        payload = [
            {"cp": c.coverpoint_id, "kind": c.kind,
             "bins": sorted(
                 [{"id": b.bin_id, "pred": b.predicate_ref,
                   "classifier": bool(b.classifier), "illegal": b.illegal,
                   "waived": b.waived} for b in c.bins],
                 key=lambda d: d["id"])}
            for c in sorted(self.coverpoints, key=lambda c: c.coverpoint_id)
        ]
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def ref(self) -> str:
        return f"coverage:{self.model_id}@v{self.version}"

    def validate_against_registry(self) -> None:
        """Fail loudly if a bin names a predicate that is not registered — never
        defer this to collection time (mirrors validate_rubric_checks)."""
        from agenttic.coverage.extractors import PREDICATES
        missing = sorted({
            b.predicate_ref for c in self.coverpoints for b in c.bins
            if b.predicate_ref and b.predicate_ref not in PREDICATES})
        if missing:
            raise ValueError(
                f"coverage model {self.model_id} v{self.version} references "
                f"unregistered predicate(s): {missing}")
