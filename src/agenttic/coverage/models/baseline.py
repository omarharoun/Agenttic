"""The baseline coverage model — applies to ANY run, for free (SPEC-13 Step 59).

The deterministic coverpoints are archetype-independent: every agent run has a
trajectory shape, took some number of model steps, met some tool condition, and
was handed data in some state. All are extracted from spans with **zero model
calls**, so this model can be applied to every run on the normal path without
adding a cent of cost or a second of latency.

`session_shape` is the one dimension here whose measurability is a fact about the
RUN rather than about the model. It carries a per-sample gate
(``session_turns_instrumented``): a scenario session records who spoke and is
measured, a stored suite case emits no turn marker and is reported NOT MEASURED.
The floor stays ``measurable=False`` so an arbitrary sample is never read. It is
kept rather than deleted because naming what cannot be measured is the product's
whole claim — deleting it would make the gap invisible instead of stated.

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
    ACTION_RISK, AGENT_STEPS, DATA_CONDITION, SESSION_SHAPE, TOOL_CONDITION,
    TRAJECTORY)
from agenttic.coverage.targets import closure_target as _target_from_config

BASELINE_MODEL_ID = "cov-baseline-deterministic"

#: what this model deliberately does not cover — printed with the numbers so a
#: baseline result is never read as a fitted one.
#  Every clause below is checkable against the model this module returns, because
#  this string is the only copy that travels with the number. "extracted
#  deterministically from the trace" was not quite true (tool_condition also
#  reads what the scenario injected), and the string was silent on the one thing
#  a reader will hit first on a black-box scan: agent_steps needs `llm_call`
#  spans, and a trace without them lands in `other`. An unexplained 0% is read as
#  "the suite never got there" — a finding someone would try to fix — when it
#  means the step count was never observable.
BASELINE_LIMITS = (
    "Baseline model: trajectory, tool condition, agent steps, data condition "
    "and the risk class of the actions the agent took — all decided by "
    "deterministic predicate, with no model call. Session shape is measured "
    "only on a run that RECORDED who spoke: a scenario session emits a turn "
    "marker before every delivery and is measured, while a stored suite case is "
    "one input handed over once and emits none, so on that path session shape "
    "is reported NOT MEASURED and left out of the closure figure rather than "
    "credited to single-turn — a trace with no turn markers is evidence of "
    "absent instrumentation, not of a single-turn session. Agent steps counts "
    "model calls, so a trace carrying no `llm_call` spans records no step count "
    "rather than a single step. It does NOT cover intent, emotional register or "
    "policy pressure, which need a fitted rubric and a calibrated classifier "
    "for this agent's archetype."
)


def baseline_model(version: int = 3, closure_target: float | None = None,
                   cfg: dict | None = None) -> CoverageModel:
    """The always-applicable deterministic coverage model.

    **v2 added ``action_risk``**: under v1 a run could trip a CRITICAL
    irreversible-action violation without closure moving a single point, because
    coverage recorded what the environment did to the agent and never what the
    agent did to the world.

    **v3 splits the old ``session_shape``.** It counted `llm_call` spans, so a
    single exchange containing a tool loop was credited as a multi-turn session.
    The step count moves to ``agent_steps``, where it is true; ``session_shape``
    keeps the turn question and is now decided per sample by the
    ``session_turns_instrumented`` gate — measured on a run that recorded who
    spoke, reported not measured on one that did not. Closure across a version
    boundary is not comparable, by construction — ``bins_fingerprint()`` changes
    so it cannot be done silently.

    ``closure_target=None`` means "ask config" (Hard Rule 7). An explicit value
    still wins, so existing callers are unaffected.
    """
    return CoverageModel(
        model_id=BASELINE_MODEL_ID,
        version=version,
        archetype_id="",                    # archetype-independent by design
        coverpoints=[TRAJECTORY, TOOL_CONDITION, AGENT_STEPS, SESSION_SHAPE,
                     DATA_CONDITION, ACTION_RISK],
        crosses=[
            # the one cross that pays for itself everywhere: did we ever see how
            # this agent behaves when a tool misbehaves?
            Cross(cross_id="tool_x_trajectory",
                  coverpoints=["tool_condition", "trajectory"], target="all"),
        ],
        closure_target=(closure_target if closure_target is not None
                        else _target_from_config(cfg)),
    )
