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
        # default: unweighted criteria get weight 1.0
        for cid in ids:
            self.weights.setdefault(cid, 1.0)
        return self
