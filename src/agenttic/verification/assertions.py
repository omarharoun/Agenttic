"""Assertions — continuous properties monitored on EVERY trace (SPEC-13 Step 62).

Criteria score a case at the end. Assertions monitor properties *throughout* the
run — including runs that pass every criterion, and including sampled live
production traffic. They are pure functions over the span sequence: no model
calls, no network, effectively free.

**The vacuity rule.** An assertion whose antecedent never occurred returns
``unexercised``, never ``pass`` (Hard Rule 60). ``never_write_without_prior_read``
on a trace containing zero writes proves nothing, and reporting it as a pass
would make the suite look clean while proving nothing. Every temporal helper here
distinguishes "the property held" from "the situation never arose".

**Violations are hard failures** (Hard Rule 59): a run scoring 1.0 on every
criterion while violating an assertion is reported FAIL, with the property named.
That verdict is computed *alongside* the scoring engine — this module never
mutates criterion scores, the weighted mean, or ``RunScore.passed``.

**The vacuity rule applies to the evaluator itself.** A predicate that RAISES
produced, until this was fixed, no result at all: :func:`evaluate` ran all eight
properties in one list comprehension, so one raising predicate destroyed the
other seven, and the only caller (``ops.verify_op``) dropped the trace with a
bare ``continue``. A trace genuinely violating ``never_write_without_prior_read``
therefore reported verdict PASS and zero violations as soon as an UNRELATED
property raised on it — and, because the signing gate binds on
``assertions.violations == 0``, that swallowed error also satisfied the gate.
So a failure to evaluate is now a first-class ``error`` result, named per
property, counted as an evaluation failure, and blocking sign-off. An evaluation
that could not run is not an evaluation that passed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Sequence

from agenttic.schema.trace import Span, Trace

#: ``error`` is not a verdict about the AGENT — it says the property could not be
#: checked on this trace. It never merges into pass/unexercised: pass would be a
#: fabricated result and unexercised would blame the suite for a defect in the
#: evaluator.
AssertionStatus = Literal["pass", "violation", "unexercised", "error"]
Severity = Literal["critical", "high", "standard"]

#: a predicate over a single span
Pred = Callable[[Span], bool]
#: an applicability test over the whole span sequence ("did the situation arise?")
Scope = Callable[[Sequence[Span]], bool]


@dataclass(frozen=True)
class AssertionResult:
    assertion_id: str
    status: AssertionStatus
    span_index: int | None      # where it broke; None if pass/unexercised
    detail: str                 # human-readable, names the property
    severity: str               # "critical" | "high" | "standard"

    @property
    def violated(self) -> bool:
        return self.status == "violation"

    @property
    def exercised(self) -> bool:
        """The property actually reached a verdict on this trace.

        ``status != "unexercised"`` would count an ``error`` as exercised, i.e.
        would let a crashed predicate raise assertion coverage.
        """
        return self.status in ("pass", "violation")

    @property
    def errored(self) -> bool:
        return self.status == "error"


@dataclass(frozen=True)
class Verdict:
    """What a temporal helper concluded, before it is dressed as a result."""

    status: AssertionStatus
    span_index: int | None = None
    note: str = ""


AssertionFn = Callable[[Trace], AssertionResult]


@dataclass(frozen=True)
class AssertionSpec:
    assertion_id: str
    fn: AssertionFn
    severity: str
    property_text: str          # the property in words, printed on violation


#: the shipped registry, mirroring scoring.checks.CHECKS
ASSERTIONS: dict[str, AssertionSpec] = {}


class UnknownAssertionError(KeyError):
    """An assertion set references an id that is not registered."""


def assertion(name: str, *, severity: Severity = "standard",
              property_text: str = "") -> Callable[[AssertionFn], AssertionFn]:
    """Register an assertion under ``name`` (mirrors the ``@check`` pattern)."""
    def deco(fn: AssertionFn) -> AssertionFn:
        if name in ASSERTIONS:
            raise ValueError(f"assertion {name!r} already registered")
        ASSERTIONS[name] = AssertionSpec(
            assertion_id=name, fn=fn, severity=severity,
            property_text=property_text or (fn.__doc__ or name).strip().split("\n")[0])
        return fn
    return deco


# --------------------------------------------------------------------------- #
# temporal helpers — pure functions over spans. Each one is vacuity-aware.
# --------------------------------------------------------------------------- #

def never(spans: Sequence[Span], forbidden: Pred, *, when: Scope) -> Verdict:
    """``never(P)`` within a scope. ``when`` decides whether the situation arose
    at all — if it did not, the verdict is ``unexercised``."""
    if not when(spans):
        return Verdict("unexercised", None, "the situation never arose")
    for i, s in enumerate(spans):
        if forbidden(s):
            return Verdict("violation", i)
    return Verdict("pass")


def always(spans: Sequence[Span], antecedent: Pred,
           consequent: Callable[[Sequence[Span], int], bool]) -> Verdict:
    """``always(antecedent -> consequent)``. ``consequent`` sees the whole span
    sequence and the antecedent's index, so it can look backwards or forwards.
    No antecedent anywhere -> ``unexercised``."""
    seen = False
    for i, s in enumerate(spans):
        if not antecedent(s):
            continue
        seen = True
        if not consequent(spans, i):
            return Verdict("violation", i)
    return Verdict("pass") if seen else Verdict(
        "unexercised", None, "the antecedent never occurred")


def precedes(spans: Sequence[Span], earlier: Pred, later: Pred) -> Verdict:
    """Every span matching ``later`` must be preceded by one matching ``earlier``.
    No ``later`` span -> ``unexercised``."""
    return always(spans, later,
                  lambda ss, i: any(earlier(ss[j]) for j in range(i)))


def within(spans: Sequence[Span], trigger: Pred, response: Pred, n: int) -> Verdict:
    """Every ``trigger`` must be followed by a ``response`` within ``n`` spans.
    No trigger -> ``unexercised``."""
    return always(spans, trigger,
                  lambda ss, i: any(response(ss[j])
                                    for j in range(i + 1, min(len(ss), i + 1 + n))))


def eventually(spans: Sequence[Span], pred: Pred, *, when: Scope) -> Verdict:
    """If the situation arose, some span must satisfy ``pred``."""
    if not when(spans):
        return Verdict("unexercised", None, "the situation never arose")
    for i, s in enumerate(spans):
        if pred(s):
            return Verdict("pass", i)
    return Verdict("violation", None)


# --------------------------------------------------------------------------- #
# turning a verdict into a reported result
# --------------------------------------------------------------------------- #

def as_result(verdict: Verdict, *, assertion_id: str, severity: str,
              property_text: str, detail: str = "") -> AssertionResult:
    """Dress a verdict as a result whose ``detail`` always names the property."""
    if verdict.status == "violation":
        where = (f" at span {verdict.span_index}"
                 if verdict.span_index is not None else "")
        text = f"VIOLATED{where}: {property_text}"
        if detail:
            text += f" — {detail}"
    elif verdict.status == "unexercised":
        text = (f"UNEXERCISED: {property_text}"
                f" ({verdict.note or 'the antecedent never occurred'})"
                " — not evidence of correctness")
    else:
        text = f"held: {property_text}"
    return AssertionResult(assertion_id=assertion_id, status=verdict.status,
                           span_index=verdict.span_index, detail=text,
                           severity=severity)


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #

def evaluation_error(spec: AssertionSpec, exc: BaseException) -> AssertionResult:
    """The one shape a property that could NOT be checked takes.

    Named after ``ops._errored_score``, and for the same reason: a failure that
    is kept and surfaced can be reasoned about, while one that is dropped shrinks
    a denominator nobody downstream can see.
    """
    return AssertionResult(
        assertion_id=spec.assertion_id, status="error", span_index=None,
        detail=(f"NOT EVALUATED: {spec.property_text} "
                f"({type(exc).__name__}: {exc}) — the property was not checked, "
                "which is not evidence that it held"),
        severity=spec.severity)


def evaluate(trace: Trace, *, assertion_ids: Sequence[str] | None = None
             ) -> list[AssertionResult]:
    """Run assertions over one trace. Pure and offline — makes no model calls, so
    it is safe to run continuously on live traffic.

    ISOLATED PER PROPERTY. This was one list comprehension, so a single raising
    predicate lost all eight results for the trace, and the caller's ``except:
    continue`` then dropped the trace entirely — turning an unrelated evaluator
    crash into "0 violations" on a trace that really did violate a property.
    Each property is now evaluated in its own try, and a failure becomes an
    ``error`` result that is reported and blocks sign-off rather than vanishing.

    An unregistered id is still a hard raise: that is a configuration error in
    the assertion SET, not a per-trace evaluation failure, and silently degrading
    it would let a typo remove a property from the battery unnoticed.
    """
    ids = list(assertion_ids) if assertion_ids is not None else list(ASSERTIONS)
    missing = [i for i in ids if i not in ASSERTIONS]
    if missing:
        raise UnknownAssertionError(f"unregistered assertion(s): {sorted(missing)}")
    out: list[AssertionResult] = []
    for i in ids:
        spec = ASSERTIONS[i]
        try:
            out.append(spec.fn(trace))
        except Exception as exc:  # noqa: BLE001 — one broken predicate loses one property
            out.append(evaluation_error(spec, exc))
    return out


def violations(results: Sequence[AssertionResult]) -> list[AssertionResult]:
    return [r for r in results if r.status == "violation"]


def unexercised(results: Sequence[AssertionResult]) -> list[AssertionResult]:
    """Assertion coverage / vacuity: which properties were never exercised. An
    unexercised assertion is NOT evidence (Hard Rule 60)."""
    return [r for r in results if r.status == "unexercised"]


def evaluation_failures(results: Sequence[AssertionResult]) -> list[AssertionResult]:
    """Properties that could not be checked at all. Reported separately from
    ``unexercised``: unexercised is a gap in the SUITE, an evaluation failure is
    a defect in the EVALUATOR, and conflating them sends the reader to the wrong
    place."""
    return [r for r in results if r.status == "error"]


def exercised_ratio(results: Sequence[AssertionResult]) -> float:
    """Assertion coverage — the share of assertions that actually got exercised."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.exercised) / len(results)


def verdict_for(results: Sequence[AssertionResult]) -> str:
    """``FAIL`` if any assertion violated, ``INCOMPLETE`` if any could not be
    evaluated, else ``PASS``. Independent of criteria scores (Hard Rule 59).

    ``INCOMPLETE`` exists because the alternative is printing PASS over a battery
    that partly did not run — the exact over-report this layer is for. It ranks
    below FAIL: a real violation is the more actionable finding.
    """
    if violations(results):
        return "FAIL"
    return "INCOMPLETE" if evaluation_failures(results) else "PASS"


def summarize(results: Sequence[AssertionResult]) -> dict:
    """Report block: total / violations / unexercised, and the named properties."""
    v, u = violations(results), unexercised(results)
    e = evaluation_failures(results)
    return {
        "total": len(results),
        "violations": len(v),
        "unexercised": len(u),
        "exercised_ratio": round(exercised_ratio(results), 4),
        "verdict": verdict_for(results),
        # Same disclosure shape the coverage leg uses for non-results
        # (samples / samples_submitted / non_results): the number that was
        # measured, the number that was asked for, and the gap, always together.
        "evaluations": len(results) - len(e),
        "evaluations_submitted": len(results),
        "evaluation_failures": len(e),
        "evaluation_failure_properties": sorted({r.assertion_id for r in e}),
        "violated_properties": [
            {"assertion_id": r.assertion_id, "severity": r.severity,
             "span_index": r.span_index, "detail": r.detail} for r in v],
        "unexercised_properties": [r.assertion_id for r in u],
    }


def rollup_assertions(results: list) -> dict:
    """Roll per-trace assertion results up PER PROPERTY across the whole run.

    A run of 20 traces × 8 properties is 160 results but only 8 properties. A
    property is VIOLATED if it broke on any trace, and UNEXERCISED only if its
    antecedent never occurred on ANY trace — reporting it as unexercised because
    most traces did not reach it would understate the evidence, and summing the
    raw results would overstate the count.

    Evaluation failures are counted and named separately, in the same shape the
    coverage leg uses for non-results (``samples`` / ``samples_submitted`` /
    ``non_results``): ``evaluations`` / ``evaluations_submitted`` /
    ``evaluation_failures``. All three travel together, always — a consumer that
    reads ``violations`` without ``evaluation_failures`` is reading a count over
    an undisclosed denominator, which is how a swallowed exception used to
    present as a clean, signable run.

    A property that ONLY ever errored is not reported as unexercised. It is not a
    gap in the suite; it is a property the evaluator failed to check, and calling
    it "never exercised" would blame the wrong component."""
    by_id: dict[str, dict] = {}
    for r in results:
        e = by_id.setdefault(r.assertion_id, {
            "assertion_id": r.assertion_id, "severity": r.severity,
            "violations": 0, "exercised": 0, "errors": 0, "traces": 0,
            "detail": "", "error_detail": ""})
        e["traces"] += 1
        if r.status == "violation":
            e["violations"] += 1
            e["exercised"] += 1
            if not e["detail"]:
                e["detail"] = r.detail
        elif r.status == "pass":
            e["exercised"] += 1
        elif r.status == "error":
            e["errors"] += 1
            if not e["error_detail"]:
                e["error_detail"] = r.detail

    violated = [e for e in by_id.values() if e["violations"]]
    errored = [e for e in by_id.values() if e["errors"]]
    unexercised = [e for e in by_id.values()
                   if e["exercised"] == 0 and e["errors"] == 0]
    total = len(by_id)
    n_failed = sum(e["errors"] for e in by_id.values())
    n_results = len(results)
    return {
        "total": total,
        "violations": len(violated),
        "unexercised": len(unexercised),
        "exercised_ratio": round((total - len(unexercised)) / total, 4) if total else 0.0,
        "verdict": ("FAIL" if violated
                    else "INCOMPLETE" if errored else "PASS"),
        # per-EVALUATION disclosure (trace x property), not per property: this is
        # the denominator `violations` was actually computed over.
        "evaluations": n_results - n_failed,
        "evaluations_submitted": n_results,
        "evaluation_failures": n_failed,
        "evaluation_failure_properties": [
            {"assertion_id": e["assertion_id"], "severity": e["severity"],
             "detail": e["error_detail"],
             "traces": f"{e['errors']}/{e['traces']} runs"} for e in errored],
        "violated_properties": [
            {"assertion_id": e["assertion_id"], "severity": e["severity"],
             "detail": e["detail"],
             "traces": f"{e['violations']}/{e['traces']} runs"} for e in violated],
        "unexercised_properties": sorted(e["assertion_id"] for e in unexercised),
    }
