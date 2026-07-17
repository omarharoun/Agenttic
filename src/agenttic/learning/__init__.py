"""Learning optimizer (SPEC-2 Step 14) — the springboard.

Closes the loop opened by Steps 11–13: human feedback + judge rationales become
a failure *dossier*, an LLM proposes candidate agent configs against it, the
existing regression-gated A/B machinery decides which survive, and survivors are
recorded in an append-only promotion ledger (:class:`AgentConfig`) with full
baseline→latest lineage. High-severity domains are held in ``pending_approval``
until a human clears them via ``agenttic learn approve``.

This deliberately REUSES the Step-10 prompt-optimizer machinery
(``reflect_on_failures``, ``PromptOptimizer.propose``, ``compare_scorecards``,
``evaluate_candidate``, ``OptimizationRun``) rather than reinventing the loop —
the learning layer adds feedback folding, the config-ledger, cost/latency
budgets, the human gate, and a preference-export for offline tuning.
"""

from agenttic.learning.optimizer import (  # noqa: F401
    AgentConfig,
    collect_failures,
    evaluate,
    export_preferences,
    gate,
    promote,
    propose,
    run_learning,
)
