"""The baseline coverage model — applies to ANY run, for free (SPEC-13 Step 59).

The four deterministic coverpoints are archetype-independent: every agent run has
a trajectory shape, met some tool condition, ran in some session shape, and was
handed data in some state. All four are extracted from spans with **zero model
calls**, so this model can be applied to every run on the normal path without
adding a cent of cost or a second of latency.

That is what lets the console answer *"what was never exercised?"* on a run the
operator has already done — instead of leading with a pass rate that is silent
about everything the suite never tried.

It is deliberately NOT a fitted model: it says nothing about intent, emotional
register, or policy pressure, because those are semantic and need a fitted,
calibrated model per archetype (SPEC-9 + the classifier-backed coverpoints). The
report labels it as baseline so a good baseline closure is never mistaken for a
verified agent.
"""

from __future__ import annotations

from agenttic.coverage.model import CoverageModel, Cross
from agenttic.coverage.models.conversational_transactional import (
    DATA_CONDITION, SESSION_SHAPE, TOOL_CONDITION, TRAJECTORY)

BASELINE_MODEL_ID = "cov-baseline-deterministic"

#: what this model deliberately does not cover — printed with the numbers so a
#: baseline result is never read as a fitted one.
BASELINE_LIMITS = (
    "Baseline model: trajectory, tool, session and data conditions only — all "
    "extracted deterministically from the trace. It does NOT cover intent, "
    "emotional register or policy pressure, which need a fitted rubric and a "
    "calibrated classifier for this agent's archetype."
)


def baseline_model(version: int = 1, closure_target: float = 0.95) -> CoverageModel:
    """The always-applicable deterministic coverage model."""
    return CoverageModel(
        model_id=BASELINE_MODEL_ID,
        version=version,
        archetype_id="",                    # archetype-independent by design
        coverpoints=[TRAJECTORY, TOOL_CONDITION, SESSION_SHAPE, DATA_CONDITION],
        crosses=[
            # the one cross that pays for itself everywhere: did we ever see how
            # this agent behaves when a tool misbehaves?
            Cross(cross_id="tool_x_trajectory",
                  coverpoints=["tool_condition", "trajectory"], target="all"),
        ],
        closure_target=closure_target,
    )
