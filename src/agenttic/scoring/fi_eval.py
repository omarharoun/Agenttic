"""Future AGI scorer — a third backend alongside code checks and the LLM judge.

Wraps Future AGI's open-source ``ai-evaluation`` library
(``from fi.evals import evaluate`` → ``result.score`` float, ``result.passed``
bool, ``result.reason`` str; Apache-2.0). Each rubric criterion with
``scorer="fi"`` names an ``fi_metric`` from ``FI_METRICS``; we call the FI
evaluator, then DISCRETIZE its 0..1 score into the criterion's binary/
three-point scale so Hard Rule 3 holds, keeping the raw score + reason in the
rationale.

``ai-evaluation`` is an OPTIONAL dependency (``pip install agenttic[fi]``),
lazy-imported only when a real evaluation runs. Tests inject a fake
``evaluate_fn`` and never import it. The default metric set targets FI's
LOCAL/offline metrics (no FI_API_KEY needed); cloud LLM-judge metrics are
marked ``local=False`` and, if used without credentials, surface as a scoring
error per case (partial batch scoring) rather than crashing the batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agenttic.schema.rubric import Rubric
from agenttic.schema.scorecard import CriterionScore
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import Trace


class FiError(RuntimeError):
    """Future AGI evaluation could not produce a usable result."""


class UnknownFiMetricError(KeyError):
    """A rubric references an fi_metric that is not registered."""


@dataclass(frozen=True)
class FiMetricSpec:
    """How to call one Future AGI metric and where its inputs come from."""

    fi_name: str                       # the metric name passed to fi.evals.evaluate
    local: bool                        # runs offline (no FI_API_KEY) ?
    description: str = ""
    # builds the kwargs dict for evaluate() from the trace + test case
    build_inputs: Callable[[Trace, TestCase], dict] | None = None


def _final_output(trace: Trace, tc: TestCase) -> dict:
    return {"output": trace.final_output, "input": _stringify(tc.input)}


def _output_vs_expected(trace: Trace, tc: TestCase) -> dict:
    expected = (tc.expected or {}).get("final_output", "")
    return {"output": trace.final_output, "expected_text": str(expected),
            "input": _stringify(tc.input)}


def _stringify(value) -> str:
    import json
    return value if isinstance(value, str) else json.dumps(value)


# Registry. ``local`` reflects FI's documented offline capability; CONFIRM the
# exact set against the installed ai-evaluation version before relying on it.
FI_METRICS: dict[str, FiMetricSpec] = {
    "contains": FiMetricSpec("contains", local=True,
                             description="Output contains the expected text.",
                             build_inputs=_output_vs_expected),
    "is_json": FiMetricSpec("is_json", local=True,
                            description="Output is valid JSON.",
                            build_inputs=_final_output),
    "similarity": FiMetricSpec("similarity", local=True,
                               description="Output is similar to the expected text.",
                               build_inputs=_output_vs_expected),
    # cloud / LLM-judge metrics (need FI_API_KEY) — available but not offline:
    "answer_relevancy": FiMetricSpec("answer_relevancy", local=False,
                                     description="Answer is relevant to the input.",
                                     build_inputs=_final_output),
    "toxicity": FiMetricSpec("toxicity", local=False,
                             description="Output is free of toxic content.",
                             build_inputs=_final_output),
    "faithfulness": FiMetricSpec("faithfulness", local=False,
                                 description="Output is faithful to its context.",
                                 build_inputs=_final_output),
}

#: metrics offered by default in the FI node (offline-safe set)
LOCAL_FI_METRICS = [name for name, spec in FI_METRICS.items() if spec.local]


def validate_rubric_fi(rubric: Rubric) -> None:
    """Fail loudly at scoring-load time if an fi criterion names an unknown
    metric (parallel to checks.validate_rubric_checks)."""
    missing = [
        (c.criterion_id, c.fi_metric)
        for c in rubric.criteria
        if c.scorer == "fi" and c.fi_metric not in FI_METRICS
    ]
    if missing:
        raise UnknownFiMetricError(
            f"rubric {rubric.rubric_id} v{rubric.version} references unknown "
            f"fi metrics: {missing}; registered: {sorted(FI_METRICS)}"
        )


def _discretize(raw: float, passed: bool, scale: str, threshold: float) -> float:
    """Map FI's continuous 0..1 score into the allowed scale (Hard Rule 3)."""
    if scale == "three_point":
        if raw >= threshold:
            return 1.0
        return 0.5 if raw >= threshold / 2 else 0.0
    # binary: prefer FI's own pass verdict, fall back to the threshold
    return 1.0 if (passed if passed is not None else raw >= threshold) else 0.0


class FiEvaluator:
    """Scores one criterion via a Future AGI metric.

    ``evaluate_fn`` defaults to a lazy import of ``fi.evals.evaluate`` — tests
    pass a fake ``(metric_name, **kwargs) -> obj`` returning ``.score`` (float),
    ``.passed`` (bool), ``.reason`` (str)."""

    def __init__(self, *, threshold: float = 0.5, evaluate_fn: Callable | None = None):
        self.threshold = threshold
        self._evaluate_fn = evaluate_fn

    @property
    def evaluate_fn(self) -> Callable:
        if self._evaluate_fn is None:
            try:
                from fi.evals import evaluate  # optional dep: agenttic[fi]
            except ImportError as exc:  # pragma: no cover - exercised via message
                raise FiError(
                    "Future AGI scorer requires the optional dependency; "
                    "install with `uv pip install agenttic[fi]` (ai-evaluation)"
                ) from exc
            self._evaluate_fn = evaluate
        return self._evaluate_fn

    def score_criterion(
        self, criterion, trace: Trace, tc: TestCase
    ) -> CriterionScore:
        if criterion.scorer != "fi":
            raise ValueError(f"criterion {criterion.criterion_id} is not fi-scored")
        spec = FI_METRICS.get(criterion.fi_metric)
        if spec is None:
            raise UnknownFiMetricError(criterion.fi_metric)
        kwargs = (spec.build_inputs or _final_output)(trace, tc)
        try:
            result = self.evaluate_fn(spec.fi_name, **kwargs)
            raw = float(result.score)
            passed = getattr(result, "passed", None)
            reason = str(getattr(result, "reason", "") or "")
        except FiError:
            raise
        except Exception as exc:  # noqa: BLE001 — normalize to FiError for partial batch
            raise FiError(f"fi metric {criterion.fi_metric!r} failed: "
                          f"{type(exc).__name__}: {exc}") from exc
        score = _discretize(raw, passed, criterion.scale, self.threshold)
        return CriterionScore(
            criterion_id=criterion.criterion_id, score=score, scorer="fi",
            judge_rationale=f"[fi:{criterion.fi_metric} raw={raw:.2f}] {reason}".strip(),
        )
