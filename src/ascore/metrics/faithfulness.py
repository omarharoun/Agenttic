"""Faithfulness / hallucination — atomic-claim groundedness (DEFERRED).

Methodology (FActScore / RAGAS-style): decompose the agent's output into atomic
claims and verify each against the task's reference context; the hallucination
rate is the unsupported fraction. Full implementation needs a claim
extractor/verifier (an LLM step), so the interface is stubbed this increment and
deferred to the next round — kept here so the metric/scorer abstraction stays
clean and extensible.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFERRED = True


@dataclass(frozen=True)
class FaithfulnessResult:
    supported: int
    total: int

    @property
    def hallucination_rate(self) -> float:
        return 0.0 if self.total == 0 else 1.0 - self.supported / self.total

    @property
    def groundedness(self) -> float:
        return 1.0 if self.total == 0 else self.supported / self.total


def score_faithfulness(output: str, reference: str, claim_checker=None) -> FaithfulnessResult:
    """Stub. Requires a ``claim_checker(claim, reference) -> bool`` (LLM-backed)
    to be supplied; not wired into the standard pipeline yet."""
    if claim_checker is None:
        raise NotImplementedError(
            "faithfulness scoring is deferred — supply a claim_checker to use it")
    claims = [c.strip() for c in output.split(".") if c.strip()]
    supported = sum(1 for c in claims if claim_checker(c, reference))
    return FaithfulnessResult(supported=supported, total=len(claims))
