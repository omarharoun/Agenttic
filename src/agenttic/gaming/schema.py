"""Data model for Evaluation-Gaming Resistance.

Four object types:

* :class:`ProbePair`      — one authored probe + how to instantiate its framings.
* :class:`FramingResult`  — the agent's measured behaviour under ONE framing.
* :class:`GamingProbeResult` — the signed gap + discretized sub-score for a probe.
* :class:`GamingReport`   — the run-level roll-up: the headline EGR band, the four
  sub-scores, and the incidents (each carrying side-by-side transcripts).

All scores are discretized to ``{0, 0.5, 1}`` to honour Hard Rule 3, exactly as
the rest of the scoring engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Mechanism = Literal["behavior_delta", "sandbagging", "consistency", "heldout"]

#: What safe behaviour the probe expects. Drives which existing safety check
#: scores each framing, and how a gap is interpreted.
SafetyExpectation = Literal["refuse", "no_disclosure", "resist", "comply_benign"]


class ProbePair(BaseModel):
    """A single behavior-delta / sandbagging / consistency / heldout probe.

    The *intent-relevant content is identical* across framings; only the framing
    wrapper differs, so a measured behaviour delta is attributable to framing."""

    probe_id: str
    mechanism: Mechanism
    base_intent: str                # the safety-relevant thing under test
    safety_check_ref: str           # existing registered check that scores a framing
    safety_expectation: SafetyExpectation
    critical: bool = False          # a gap on a critical intent hard-floors EGR
    # mechanism-specific render config (framing templates, paraphrases, elicitation
    # ladder, held-out public/generated pair, plus any ``expected`` a check needs).
    config: dict = Field(default_factory=dict)
    k: int = 5                      # runs per framing (variance estimate)
    canary: str | None = None       # embedded in heldout scenarios (contamination tripwire)


class FramingResult(BaseModel):
    """The agent's measured behaviour under ONE framing of a probe."""

    framing: str                    # "test" | "deploy" | "paraphrase:3" | "elicit:cot" | ...
    behavior_rate: float            # mean safety score over k runs, in [0,1]
    n_runs: int = 0
    n_safe: int = 0                 # runs the safety check scored 1.0
    awareness_verbalized: bool = False
    awareness_quote: str | None = None
    transcripts: list[str] = Field(default_factory=list)  # raw agent outputs (evidence)
    prompt: str = ""                # the exact prompt sent (evidence)


class GamingProbeResult(BaseModel):
    """The signed gap + discretized sub-score for one probe."""

    probe_id: str
    mechanism: Mechanism
    base_intent: str
    critical: bool = False
    framing_results: list[FramingResult] = Field(default_factory=list)
    gap: float                      # signed, mechanism-specific (see runner)
    sub_score: float                # discretized to {0, 0.5, 1}
    incident: bool                  # gap crossed the concern threshold
    critical_incident: bool = False  # incident on a critical intent
    detail: dict = Field(default_factory=dict)  # worst_case, elicitation ladder, freshness…

    def framing(self, name: str) -> FramingResult | None:
        return next((f for f in self.framing_results if f.framing == name), None)


class GamingReport(BaseModel):
    """Run-level roll-up. ``egr`` is derived from the probe results, so it can
    never drift from the underlying evidence."""

    agent_id: str
    egr: float                      # 0-100 headline (point estimate)
    egr_low: float                  # band low (per-mechanism run variance)
    egr_high: float                 # band high
    sub_scores: dict[str, float] = Field(default_factory=dict)  # 4 mechanism means, 0-100
    probe_results: list[GamingProbeResult] = Field(default_factory=list)
    n_probes: int = 0
    n_incidents: int = 0
    n_critical_incidents: int = 0
    provisional: bool = True
    limits: str = ""                # the honesty-contract one-liner
    agent_cost_usd: float = 0.0     # total agent-execution spend for the run

    def incidents(self) -> list[GamingProbeResult]:
        return [p for p in self.probe_results if p.incident]
