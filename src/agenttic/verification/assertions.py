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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Sequence

from agenttic.schema.trace import Span, Trace

AssertionStatus = Literal["pass", "violation", "unexercised"]
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
        return self.status != "unexercised"


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

def evaluate(trace: Trace, *, assertion_ids: Sequence[str] | None = None
             ) -> list[AssertionResult]:
    """Run assertions over one trace. Pure and offline — makes no model calls, so
    it is safe to run continuously on live traffic."""
    ids = list(assertion_ids) if assertion_ids is not None else list(ASSERTIONS)
    missing = [i for i in ids if i not in ASSERTIONS]
    if missing:
        raise UnknownAssertionError(f"unregistered assertion(s): {sorted(missing)}")
    return [ASSERTIONS[i].fn(trace) for i in ids]


def violations(results: Sequence[AssertionResult]) -> list[AssertionResult]:
    return [r for r in results if r.status == "violation"]


def unexercised(results: Sequence[AssertionResult]) -> list[AssertionResult]:
    """Assertion coverage / vacuity: which properties were never exercised. An
    unexercised assertion is NOT evidence (Hard Rule 60)."""
    return [r for r in results if r.status == "unexercised"]


def exercised_ratio(results: Sequence[AssertionResult]) -> float:
    """Assertion coverage — the share of assertions that actually got exercised."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.exercised) / len(results)


def verdict_for(results: Sequence[AssertionResult]) -> str:
    """``FAIL`` if any assertion violated, else ``PASS``. Independent of criteria
    scores (Hard Rule 59)."""
    return "FAIL" if violations(results) else "PASS"


def summarize(results: Sequence[AssertionResult]) -> dict:
    """Report block: total / violations / unexercised, and the named properties."""
    v, u = violations(results), unexercised(results)
    return {
        "total": len(results),
        "violations": len(v),
        "unexercised": len(u),
        "exercised_ratio": round(exercised_ratio(results), 4),
        "verdict": verdict_for(results),
        "violated_properties": [
            {"assertion_id": r.assertion_id, "severity": r.severity,
             "span_index": r.span_index, "detail": r.detail} for r in v],
        "unexercised_properties": [r.assertion_id for r in u],
    }
