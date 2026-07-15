"""Normalized evaluator result — the single row every evaluator source emits.

This is the interop keystone of the Evaluator Plugin Interface: many
heterogeneous evaluators (Agenttic's own red-team generator, AISI's Inspect,
future third-party adapters) all normalize their native output into a
:class:`EvalResult`. A single signed passport can then attest to the *union* of
those sources — honestly, because every row carries its own provenance and the
evaluator's original output verbatim.

Design invariants (non-negotiable):

* **Provenance is never optional.** ``source`` + ``source_version`` +
  ``source_license`` (SPDX) travel on every row. You can always trace a verdict
  back to the exact evaluator build and its license.
* **The controlled ``dimension`` vocabulary is owned by Agenttic** (defined once,
  here — :data:`DIMENSIONS`). Each adapter maps its native categories onto this
  vocabulary through a *versioned* mapping table living in the adapter, and keeps
  the native value in ``raw``. We never invent a dimension per source.
* **``raw`` is the evaluator's ORIGINAL output, verbatim.** Normalization is
  additive: we never destroy the source's own record of what it decided.
* **An agent mistake is data, not a crash.** ``outcome="error"`` is a first-class
  verdict for a case the evaluator could not run to completion; the score is then
  ``None`` and the run continues.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# --------------------------------------------------------------------------- #
# Controlled dimension vocabulary — Agenttic OWNS this. One place, versioned.
# Adapters map their native categories onto these ids; they never add their own.
# --------------------------------------------------------------------------- #

#: Bumped when a dimension id is added/removed/renamed, so a dossier records the
#: exact vocabulary it was graded against (an old passport stays interpretable).
DIMENSION_VOCAB_VERSION = "agenttic-dimensions/v1"

#: The canonical safety/quality dimensions a passport can decompose into. Kept
#: deliberately small and stable; extend by MINOR bump (add an id + description).
DIMENSION_DESCRIPTIONS: dict[str, str] = {
    "injection_robustness":
        "Resists prompt-injection / instruction-hijack carried in tool results, "
        "retrieved documents, or user text (AgentDojo / InjecAgent family).",
    "harmful_refusal":
        "Refuses to carry out harmful or disallowed actions/requests "
        "(AgentHarm / refusal family).",
    "tool_safety":
        "Does not invoke dangerous, forbidden, or unauthorized tools, and does "
        "not perform destructive actions.",
    "secret_disclosure":
        "Does not leak secrets, credentials, or confidential context it holds.",
    "faithfulness":
        "Answers are grounded in the provided context/tools; does not fabricate "
        "or contradict its evidence (hallucination family).",
}

#: The frozen set of legal ``dimension`` values.
DIMENSIONS: frozenset[str] = frozenset(DIMENSION_DESCRIPTIONS)

Outcome = Literal["pass", "fail", "error", "not_assessed"]
_OUTCOMES: frozenset[str] = frozenset({"pass", "fail", "error", "not_assessed"})

Oracle = Literal["deterministic", "judge", "vendor"]
_ORACLES: frozenset[str] = frozenset({"deterministic", "judge", "vendor"})


class EvalResultError(ValueError):
    """A row violated a normalized-result invariant (bad provenance/vocab/verdict)."""


@dataclass(frozen=True)
class EvalResult:
    """One evaluator's verdict on one probe against one agent, normalized.

    Frozen: a result is immutable evidence once emitted. ``raw`` stays as the
    evaluator gave it — we validate the *normalized* fields, never rewrite the
    source's own record.
    """

    # -- provenance (NEVER optional; SPDX for the license) ------------------- #
    source: str            # evaluator id, e.g. "agenttic-gen" / "inspect_ai"
    source_version: str    # exact evaluator build/version string
    source_license: str    # SPDX identifier, e.g. "MIT" / "Apache-2.0"

    # -- what was tested ----------------------------------------------------- #
    dimension: str         # MUST be in DIMENSIONS (Agenttic's controlled vocab)
    test_id: str           # evaluator-local id of the probe/case
    probe: str             # human-readable description of the probe/attack

    # -- verdict ------------------------------------------------------------- #
    outcome: Outcome       # pass | fail | error | not_assessed
    score: float | None    # 0..1, or None (None for error / not_assessed / N/A)
    raw: dict[str, Any]    # the evaluator's ORIGINAL output, verbatim

    # -- evidence ------------------------------------------------------------ #
    oracle: Oracle         # deterministic | judge | vendor
    rationale: str = ""    # short why-string (may echo the source's explanation)
    trace_ref: str | None = None  # id of the Trace this verdict was drawn from

    def __post_init__(self) -> None:
        for name in ("source", "source_version", "source_license"):
            if not str(getattr(self, name) or "").strip():
                raise EvalResultError(
                    f"EvalResult.{name} is required provenance and must be "
                    "non-empty (source/source_version/source_license are never "
                    "optional)")
        if self.dimension not in DIMENSIONS:
            raise EvalResultError(
                f"dimension {self.dimension!r} is not in Agenttic's controlled "
                f"vocabulary {sorted(DIMENSIONS)} — adapters must MAP onto it, "
                "not invent dimensions")
        if self.outcome not in _OUTCOMES:
            raise EvalResultError(
                f"outcome {self.outcome!r} not in {sorted(_OUTCOMES)}")
        if self.oracle not in _ORACLES:
            raise EvalResultError(
                f"oracle {self.oracle!r} not in {sorted(_ORACLES)}")
        if self.score is not None:
            s = float(self.score)
            if not (0.0 <= s <= 1.0):
                raise EvalResultError(
                    f"score {self.score!r} must be within [0,1] or None")
        # A concrete pass/fail with no score, or an error carrying a score, are
        # both incoherent — keep the verdict and the number consistent.
        if self.outcome in ("error", "not_assessed") and self.score is not None:
            raise EvalResultError(
                f"outcome={self.outcome} must carry score=None (a case that did "
                "not run to a verdict has no score)")
        if not isinstance(self.raw, dict):
            raise EvalResultError("raw must be a dict (the source's verbatim output)")

    @property
    def assessed(self) -> bool:
        """True when this row is a real measurement (pass/fail), not error/skip."""
        return self.outcome in ("pass", "fail")

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict for embedding in a dossier / API payload."""
        return {
            "source": self.source,
            "source_version": self.source_version,
            "source_license": self.source_license,
            "dimension": self.dimension,
            "test_id": self.test_id,
            "probe": self.probe,
            "outcome": self.outcome,
            "score": self.score,
            "raw": self.raw,
            "oracle": self.oracle,
            "rationale": self.rationale,
            "trace_ref": self.trace_ref,
        }
