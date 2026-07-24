"""Stimulus — constrained-random scenario generation (SPEC-13 Step 60).

Two stages, and the split is architectural: `space` is PURE seeded code that must
never import a model client; `realize` is the only module that touches a model.
`oracle` derives the expected outcome from the abstract point plus the policy —
a rule table, never a model call.
"""

from agenttic.stimulus.oracle import (  # noqa: F401
    Expectation, PolicyDoc, derive_expectation)
from agenttic.stimulus.space import (  # noqa: F401
    AbstractPoint, BinRef, Dimension, Illegal, Implies, Requires, ScenarioSpace,
    sample_batch, sample_point, sample_point_targeting, satisfies, violations)

__all__ = ["AbstractPoint", "BinRef", "Dimension", "Illegal", "Implies",
           "Requires", "ScenarioSpace", "sample_batch", "sample_point",
           "sample_point_targeting", "satisfies", "violations",
           "Expectation", "PolicyDoc", "derive_expectation"]
