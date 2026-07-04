"""Evaluation-Gaming Resistance (EGR) — detect when an agent behaves differently
because it senses it is being evaluated.

Black-box probes over four mechanisms (behavior-delta / eval-awareness,
sandbagging / capability-elicitation, cross-framing consistency, held-out
procedural scenarios), rolled into ONE provisional headline metric.

HONESTY CONTRACT (see docs/GAMING_SPEC.md §4.3): a high EGR proves only the
*absence of detectable gaming under our probes*. It does NOT prove the agent is
honest, non-scheming, or non-sandbagging — a uniformly deceptive or *silently*
eval-aware agent produces a clean EGR. EGR therefore ships PROVISIONAL
(calibrated=False), stays out of the weighted Agenttic Index (weight 0), and its
copy states its limits inline.
"""

from ascore.gaming.schema import (
    FramingResult,
    GamingProbeResult,
    GamingReport,
    ProbePair,
)

__all__ = [
    "FramingResult",
    "GamingProbeResult",
    "GamingReport",
    "ProbePair",
]
