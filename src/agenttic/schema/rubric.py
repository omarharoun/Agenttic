"""Rubric schema — what gets scored and how.

Hard Rule 2: judge criteria without pass/fail anchors are invalid (load-time error).
Hard Rule 3: scales are binary or three_point only. No 1-10 scoring anywhere.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Criterion(BaseModel):
    """One scored dimension of agent behaviour."""

    criterion_id: str
    description: str
    scorer: Literal["code", "judge", "fi"]
    scale: Literal["binary", "three_point"]
    check_ref: str | None = None  # required when scorer == "code"
    fi_metric: str | None = None  # required when scorer == "fi" (a Future AGI metric)
    anchors: dict = Field(default_factory=dict)  # required keys for judge: "pass", "fail"
    tags: list[str] = Field(default_factory=list)  # e.g. "trajectory", "live"
    #: F2a — declarative applicability predicate. None => the criterion always
    #: applies (backward compatible). Otherwise the criterion applies to a case
    #: only when the situation it checks is present, so it is not scored 0 on a
    #: case where nothing to check ever arose (recorded N/A instead). Allowed
    #: predicates (ANY match ⇒ applies): ``case_tags_any`` (the case carries one
    #: of these tags) and ``expected_present`` (the case's ``expected`` has one of
    #: these keys, truthy). Deterministic: read only from the case.
    applies_when: dict | None = None

    # LLM output and older records often carry an explicit ``null`` for these
    # optional containers; default_factory only fills a MISSING key, so coerce
    # None -> empty here rather than crashing with a cryptic dict/list type
    # error. Hard Rule 2 below still rejects a judge criterion with no real
    # pass/fail anchors — with a clear message instead of a validation dump.
    @field_validator("anchors", mode="before")
    @classmethod
    def _anchors_none_to_empty(cls, v: object) -> object:
        return {} if v is None else v

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_none_to_empty(cls, v: object) -> object:
        return [] if v is None else v

    @field_validator("applies_when", mode="before")
    @classmethod
    def _validate_applies_when(cls, v: object) -> object:
        # Reject typos at load time: an unknown predicate key would otherwise be
        # silently ignored and the criterion would always apply (fail open).
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("applies_when must be a dict of {predicate: [values]}")
        allowed = {"case_tags_any", "expected_present"}
        unknown = set(v) - allowed
        if unknown:
            raise ValueError(
                f"applies_when has unknown predicate(s) {sorted(unknown)}; "
                f"allowed: {sorted(allowed)}")
        for k, vals in v.items():
            if not isinstance(vals, list) or not all(isinstance(x, str) for x in vals):
                raise ValueError(f"applies_when[{k!r}] must be a list of strings")
        return v

    @model_validator(mode="after")
    def _scorer_requirements(self) -> "Criterion":
        if self.scorer == "judge":
            missing = {"pass", "fail"} - set(self.anchors)
            if missing:
                raise ValueError(
                    f"criterion {self.criterion_id}: judge criteria require "
                    f"pass/fail anchors; missing {sorted(missing)} (Hard Rule 2)"
                )
        if self.scorer == "code" and not self.check_ref:
            raise ValueError(
                f"criterion {self.criterion_id}: code criteria require check_ref"
            )
        if self.scorer == "fi" and not self.fi_metric:
            raise ValueError(
                f"criterion {self.criterion_id}: fi criteria require fi_metric"
            )
        return self


class Rubric(BaseModel):
    """A versioned set of criteria with aggregation weights."""

    rubric_id: str
    version: int = 1
    criteria: list[Criterion]
    weights: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _weights_match_criteria(self) -> "Rubric":
        if not self.criteria:
            raise ValueError(f"rubric {self.rubric_id}: criteria must be non-empty")
        ids = {c.criterion_id for c in self.criteria}
        if len(ids) != len(self.criteria):
            raise ValueError(f"rubric {self.rubric_id}: duplicate criterion_id")
        unknown = set(self.weights) - ids
        if unknown:
            raise ValueError(
                f"rubric {self.rubric_id}: weights reference unknown criteria "
                f"{sorted(unknown)}"
            )
        # Default: unweighted criteria get weight 1.0 — filled in CRITERION
        # ORDER, not set order. `ids` is a set, so iterating it inserts the
        # keys in string-hash order, and PYTHONHASHSEED is randomised per
        # process: the same built-in rubric serialised to different bytes in
        # different processes. That is a reproducibility defect on its own (a
        # scorecard names `rubric v1`, and v1's bytes depended on which process
        # happened to write it), and it is what made concurrent cold starts
        # fail even once the insert race was closed. The weights are identical
        # either way — only their order moved.
        for c in self.criteria:
            self.weights.setdefault(c.criterion_id, 1.0)
        return self
