"""Faithfulness / hallucination — atomic-claim groundedness.

Methodology — FActScore (Min et al., 2023) / RAGAS faithfulness / MIRAGE-Bench:
decompose the agent's output into ATOMIC factual claims, then verify each claim
against the task's reference context (the provided ground truth). The
*faithfulness* (a.k.a. groundedness) score is the supported fraction; the
*hallucination rate* is its complement — the fraction of claims the reference
context does NOT support.

The verification step is LLM-backed (a claim-checker), reusing the platform's
judge/LLM client plumbing (see :func:`make_llm_claim_checker`, wired through the
standard runner from the tenant's own Anthropic key). It degrades gracefully and
*labels* the degradation when a case has no reference context (``no_reference``)
or the output yields no claims (``no_claims``).

Atomic-claim decomposition defaults to a deterministic sentence splitter so the
metric is exercisable without a second LLM round-trip; an LLM ``claim_extractor``
can be supplied for finer FActScore-style atomicity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

#: a claim-checker decides whether one atomic claim is entailed by the reference.
ClaimChecker = Callable[[str, str], bool]
#: a claim-extractor decomposes an output string into atomic claim strings.
ClaimExtractor = Callable[[str], "list[str]"]

DEFERRED = False  # implemented — kept for back-compat with earlier imports

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class FaithfulnessResult:
    """One output scored against one reference context.

    ``status`` distinguishes a genuinely scored case from the labeled
    degradations so the rollup never silently treats "couldn't check" as 1.0.
    """

    supported: int
    total: int
    status: str = "scored"  # scored | no_reference | no_claims
    claims: tuple[str, ...] = ()
    unsupported_claims: tuple[str, ...] = ()

    @property
    def groundedness(self) -> float | None:
        """Supported fraction in [0,1]; ``None`` when unverifiable (no reference)."""
        if self.status == "no_reference":
            return None
        if self.total == 0:  # no claims -> vacuously grounded (nothing to hallucinate)
            return 1.0
        return self.supported / self.total

    # FActScore / RAGAS call this "faithfulness"; same number, literature name.
    @property
    def faithfulness(self) -> float | None:
        return self.groundedness

    @property
    def hallucination_rate(self) -> float | None:
        """Unsupported fraction in [0,1]; ``None`` when unverifiable."""
        g = self.groundedness
        return None if g is None else 1.0 - g

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "supported": self.supported,
            "total": self.total,
            "groundedness": self.groundedness,
            "hallucination_rate": self.hallucination_rate,
            "unsupported_claims": list(self.unsupported_claims),
        }


@dataclass(frozen=True)
class FaithfulnessAggregate:
    """Roll-up of many :class:`FaithfulnessResult` into the index component."""

    groundedness: float | None      # macro-mean over scored cases ([0,1] or None)
    hallucination_rate: float | None
    scored_cases: int
    no_reference_cases: int
    supported_claims: int
    total_claims: int
    mode: str = "scored"            # scored | no_reference (nothing verifiable)


def split_sentences(text: str) -> list[str]:
    """Deterministic atomic-claim fallback: sentence/line split, trimmed."""
    text = (text or "").strip()
    if not text:
        return []
    out = []
    for part in _SENT_SPLIT.split(text):
        c = part.strip().strip("-•* ").rstrip(".").strip()
        if c:
            out.append(c)
    return out


def extract_atomic_claims(output: str, *, extractor: ClaimExtractor | None = None) -> list[str]:
    """Decompose ``output`` into atomic factual claims (FActScore step 1).

    Uses ``extractor`` (LLM-backed) when supplied, else a sentence splitter."""
    if extractor is not None:
        return [c.strip() for c in extractor(output) if c and c.strip()]
    return split_sentences(output)


def score_faithfulness(
    output: str,
    reference: str,
    claim_checker: ClaimChecker | None = None,
    *,
    claim_extractor: ClaimExtractor | None = None,
) -> FaithfulnessResult:
    """Score one output against ``reference`` (FActScore / RAGAS faithfulness).

    Requires an LLM-backed ``claim_checker(claim, reference) -> bool``. When the
    reference context is empty the result is labeled ``no_reference`` (groundedness
    is unknown, not 1.0) so the caller can exclude it honestly."""
    if claim_checker is None:
        raise NotImplementedError(
            "faithfulness scoring needs a claim_checker(claim, reference) -> bool "
            "(LLM-backed); use make_llm_claim_checker or inject one")
    if not (reference and reference.strip()):
        return FaithfulnessResult(supported=0, total=0, status="no_reference")
    claims = extract_atomic_claims(output, extractor=claim_extractor)
    if not claims:
        return FaithfulnessResult(supported=0, total=0, status="no_claims")
    supported = 0
    unsupported: list[str] = []
    for claim in claims:
        if claim_checker(claim, reference):
            supported += 1
        else:
            unsupported.append(claim)
    return FaithfulnessResult(
        supported=supported, total=len(claims), status="scored",
        claims=tuple(claims), unsupported_claims=tuple(unsupported))


def aggregate_faithfulness(results: list[FaithfulnessResult]) -> FaithfulnessAggregate:
    """Combine per-case results into the Agenttic Index component.

    Macro-averages per-case groundedness over the cases that were actually
    scored (``no_reference`` cases are counted and reported, never folded in as a
    pass). Returns ``mode='no_reference'`` when nothing was verifiable."""
    scored = [r for r in results if r.status != "no_reference"]
    no_ref = [r for r in results if r.status == "no_reference"]
    if not scored:
        return FaithfulnessAggregate(
            groundedness=None, hallucination_rate=None, scored_cases=0,
            no_reference_cases=len(no_ref), supported_claims=0, total_claims=0,
            mode="no_reference")
    per_case = [r.groundedness for r in scored]  # each in [0,1] (None excluded above)
    g = sum(per_case) / len(per_case)
    return FaithfulnessAggregate(
        groundedness=g, hallucination_rate=1.0 - g, scored_cases=len(scored),
        no_reference_cases=len(no_ref),
        supported_claims=sum(r.supported for r in scored),
        total_claims=sum(r.total for r in scored), mode="scored")


# -- LLM claim-checker (reuses the judge/LLM client plumbing) ----------------

_CHECK_SYSTEM = (
    "You verify factual groundedness. Given a REFERENCE CONTEXT and a single "
    "atomic CLAIM, decide whether the claim is SUPPORTED by (entailed by) the "
    "reference context. If the context is silent on the claim or contradicts it, "
    "the claim is NOT supported. Respond with ONLY a JSON object "
    '{"supported": true} or {"supported": false} and nothing else.')


def make_llm_claim_checker(client, model: str, *, max_tokens: int = 20,
                           retry_policy=None) -> ClaimChecker:
    """Build a claim-checker over an Anthropic-style ``client`` (one call/claim).

    Used by the standard runner with the tenant's own key (BYO-key path). The
    model must differ from the agent-under-test (Hard Rule 4); the runner picks
    the judge model and enforces that before calling here."""
    def _create(prompt: str):
        call = lambda: client.messages.create(  # noqa: E731
            model=model, max_tokens=max_tokens, system=_CHECK_SYSTEM,
            messages=[{"role": "user", "content": prompt}])
        if retry_policy is not None:
            from agenttic.retry import with_retry
            return with_retry(call, retry_policy, op="faithfulness-claim")
        return call()

    def checker(claim: str, reference: str) -> bool:
        prompt = (f"REFERENCE CONTEXT:\n{reference}\n\nCLAIM:\n{claim}\n\n"
                  "Is the claim supported by the reference context?")
        resp = _create(prompt)
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        try:
            return bool(json.loads(text)["supported"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            low = text.lower()  # lenient fallback if the judge wrapped the JSON
            return "true" in low and "false" not in low

    return checker


_EXTRACT_SYSTEM = (
    "Decompose the TEXT into a list of atomic factual claims — each a single, "
    "self-contained, independently checkable statement. Respond with ONLY a JSON "
    'array of strings, e.g. ["claim one", "claim two"].')


def make_llm_claim_extractor(client, model: str, *, max_tokens: int = 512,
                             retry_policy=None) -> ClaimExtractor:
    """Optional FActScore-style LLM atomic-claim extractor (else sentence split)."""
    def extractor(output: str) -> list[str]:
        call = lambda: client.messages.create(  # noqa: E731
            model=model, max_tokens=max_tokens, system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": f"TEXT:\n{output}"}])
        if retry_policy is not None:
            from agenttic.retry import with_retry
            resp = with_retry(call, retry_policy, op="faithfulness-extract")
        else:
            resp = call()
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(c) for c in data]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return split_sentences(output)  # graceful fallback

    return extractor
