"""Human feedback — the new contract for the learning loop (SPEC-2 Step 11).

Humans stop being a fallback and become part of the data engine: reviewer
approvals, end-user corrections, ratings on specific criteria, and escalation
decisions all become labeled experience data with provenance.

Design invariants (non-negotiable — Hard Rule 11):

* **Feedback is data with provenance.** ``source`` and ``rationale`` are always
  present, so any label can be traced back to who gave it and why.
* **Humans obey the same scale as judges (Hard Rule 3).** A human ``rating`` is
  in ``{0, 0.5, 1}`` — the same three-point scale the LLM judge uses — so human
  and machine labels are directly comparable in the calibration set.
* **A correction carries its ground truth.** ``kind="correction"`` requires the
  ``corrected_output`` it corrects to; a ``rating`` requires the ``criterion_id``
  it rates and a value on the scale.

This schema is independent of the trace schema, so it carries its own version
(``FEEDBACK_SCHEMA_VERSION``) and does NOT bump ``trace.SCHEMA_VERSION``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator

#: Bumped when a HumanFeedback field is added/removed/renamed, so stored feedback
#: stays interpretable across versions (independent of trace.SCHEMA_VERSION).
FEEDBACK_SCHEMA_VERSION = "0.1.0"

#: The three-point scale human ratings obey — identical to the judge's scale
#: (Hard Rule 3 applies to humans too), so labels are directly comparable.
RATING_SCALE: tuple[float, ...] = (0.0, 0.5, 1.0)

FeedbackSource = Literal["reviewer", "end_user", "escalation"]
FeedbackKind = Literal["approval", "correction", "rating", "escalation_decision"]


class HumanFeedback(BaseModel):
    """One piece of human feedback on a specific trace (a scored agent run).

    Persisted append-only in the registry (``FeedbackRow``); mined into draft
    test cases + calibration labels by the feedback→tests pipeline (Step 13),
    and folded into failure dossiers by the learning optimizer (Step 14).
    """

    feedback_id: str
    trace_id: str
    agent_id: str
    source: FeedbackSource
    kind: FeedbackKind
    criterion_id: str | None = None
    rating: float | None = None
    corrected_output: str | None = None
    rationale: str
    created_at: datetime

    @model_validator(mode="after")
    def _check_kind_requirements(self) -> "HumanFeedback":
        # A correction must carry the corrected output it proposes as truth.
        if self.kind == "correction" and not (self.corrected_output or "").strip():
            raise ValueError(
                "kind='correction' requires a non-empty corrected_output")
        # A rating must name the criterion it rates AND sit on the shared scale.
        if self.kind == "rating":
            if not (self.criterion_id or "").strip():
                raise ValueError("kind='rating' requires a criterion_id")
            if self.rating is None:
                raise ValueError("kind='rating' requires a rating")
        # Any rating that IS given (regardless of kind) obeys the judge's scale.
        if self.rating is not None and self.rating not in RATING_SCALE:
            raise ValueError(
                f"rating {self.rating} is off the {RATING_SCALE} scale — human "
                "ratings obey the same three-point scale as judges (Hard Rule 3)")
        return self
