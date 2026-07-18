"""Judge-optimization requests — the calibration flywheel's trigger record
(SPEC-3 Step 15.4).

When new human labels arrive, the platform NOTICES that a judge criterion needs
re-optimizing and FILES A REQUEST — it never auto-runs the optimizer. This is
the judge analogue of Step 9's drift-triggered re-eval: detection is automatic,
the fix stays on-command via ``learn-judge``.

A :class:`JudgeOptimizationRequest` is an open todo: "criterion X's judge should
be re-optimized, because Y". It is created as a side effect of ``mine_labels``
(the hook point where new labels land) and CLEARED when a ``run_judge_learning``
round runs for that criterion. Requests are tenant-scoped and de-duplicated:
at most ONE ``status="open"`` request exists per criterion at a time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

JudgeOptimizationRequestStatus = Literal["open", "cleared"]


class JudgeOptimizationRequest(BaseModel):
    """One outstanding "please re-optimize this judge" request.

    ``reason`` is a human-readable trigger message, e.g.
    ``"criterion crossed min_labels (20 labels)"`` or
    ``"agreement 0.62 dropped below threshold 0.80 on 34 labels"``. ``status``
    is ``open`` until a learning round clears it (``cleared``). Only ONE open
    request exists per (tenant, criterion) — new triggers refresh the existing
    open request rather than stacking duplicates.
    """

    request_id: str
    criterion_id: str
    suite_id: str = ""
    reason: str = ""
    status: JudgeOptimizationRequestStatus = "open"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cleared_at: datetime | None = None
