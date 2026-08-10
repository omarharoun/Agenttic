"""Suite-validation gate (SPEC-6 oracle gate) — run BEFORE a suite is approved.

The first real evaluation exposed that this gate never ran: approval
(``approve_suite``) was an unconditional boolean flip, and the only reference-gate
machinery (:mod:`agenttic.verification.selfcheck`) ran at test time over *shipped*
suites, never a runtime-generated one. So a suite whose judge criteria checked a
*reaction to a situation* (refusal, scope-boundary, exfiltration-decline) was
approved and scored those criteria 0.0 on benign cases where the situation never
arose — measuring a rubric/case mismatch, not the agent.

This module is the gate that should have fired. It is deterministic and offline
(no model, no judge, no spend): it reasons about *applicability*, which — after
F2a — is decidable from the case alone. Two failure modes it refuses:

* **Conditional criterion, no applicability declared.** A judge criterion that
  reads as conditional (it checks a reaction to a situation) but carries neither
  an ``applies_when`` predicate nor a ``refusal``/``safety`` tag, applied to a
  benign case. It will be scored 0 there. The fix names the remedy: declare
  ``applies_when`` (F2a then records it N/A) or tag it.

* **Dead criterion.** A criterion whose applicability constraint matches *no*
  case in the suite — it is N/A on every case, so it measures nothing.

**Honest limit (the latent bug the handover names).** A fully general oracle —
"run the reference solution and confirm it passes its own checks" — must score
judge criteria, which needs a model/judge. That judge-enabled reference run is
the complete check and is not runnable offline; this deterministic gate catches
the mismatch *structurally* instead (an undeclared conditional criterion), which
is enough to refuse the suites that caused the defect. When a judge is available,
the reference-oracle pass should run in addition, not instead.
"""

from __future__ import annotations

from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import SCHEMA_VERSION, Trace
from agenttic.scoring.checks import CheckConfigError, repair_expected, run_check
from agenttic.scoring.engine import (
    _is_refusal_criterion,
    applicable_to_case,
    case_warrants_refusal,
    criterion_applies_to_case,
)

#: Substrings that mark a criterion as *conditional* — it grades a reaction to a
#: situation, so it is meaningless (and scores 0) when the situation is absent.
#: A pragmatic smell-check, matched against id + description + anchors. It is not
#: exhaustive; the exhaustive check is the judge-enabled reference oracle. Keeping
#: it small and explicit (like the check-defaults table) so the policy is auditable.
_CONDITIONAL_HINTS = (
    "refus", "decline", "scope_boundary", "scope boundary", "out-of-scope",
    "out of scope", "out_of_scope", "boundary of its scope", "exfiltrat",
    "unauthorized", "unauthorised", "resist", "not tempted",
)


class SuiteValidationError(ValueError):
    """A suite failed pre-run validation. The message names every offending
    criterion/case so a missing declaration is fixed, not merely reported."""


def _has_applicability_declaration(c: Criterion) -> bool:
    """True when the criterion states when it applies — an ``applies_when``
    predicate, or a refusal/safety tag/check_ref the engine already gates on."""
    return bool(getattr(c, "applies_when", None)) or _is_refusal_criterion(c)


def _looks_conditional(c: Criterion) -> bool:
    hay = " ".join([
        c.criterion_id, c.description or "",
        " ".join(str(v) for v in (c.anchors or {}).values()),
    ]).lower()
    return any(h in hay for h in _CONDITIONAL_HINTS)


def _probe_trace() -> Trace:
    """A minimal, answer-less trace used only to trigger a code check's config
    validation (its ``_need`` guard) without running the agent."""
    return Trace(trace_id="suite-validation-probe", agent_id="probe",
                 agent_config_hash="probe", test_case_id="probe", spans=[],
                 visibility="black_box", final_output="", schema_version=SCHEMA_VERSION)


def _missing_required_key_problems(rubric: Rubric, cases: list[TestCase]) -> list[str]:
    """F3 — a code check whose REQUIRED expected key has no safe default must fail
    validation before the run. We mirror scoring exactly: ``repair_expected`` fills
    the defaulted keys first, then we run each code check against a minimal trace.
    A check that has a safe default no longer raises; one that does not (e.g.
    ``final_output_matches_expected``) raises ``CheckConfigError``, whose message
    already names the case and the missing key. Deriving it from the real check
    (not a hand-kept table) means it cannot drift from what actually errors."""
    problems: list[str] = []
    probe = _probe_trace()
    code_criteria = [c for c in rubric.criteria if c.scorer == "code" and c.check_ref]
    if not code_criteria:
        return problems
    for tc in cases:
        repaired = tc.model_copy(
            update={"expected": repair_expected(tc.expected, rubric)})
        for c in code_criteria:
            try:
                run_check(c.check_ref, probe, repaired)
            except CheckConfigError as exc:
                # message: "test <id>: check requires expected['<key>']"
                problems.append(f"missing expectation — {exc}")
            except Exception:  # noqa: BLE001 — other errors are not missing-config
                pass
    return problems


def validate_suite(rubric: Rubric, cases: list[TestCase]) -> list[str]:
    """Return a list of suite-design problems (empty ⇒ the suite passes the gate).

    Deterministic and offline. Each problem string names the criterion and, where
    relevant, an example case, plus the remedy."""
    problems: list[str] = []
    cases = list(cases)

    # F3 — required expected keys with no safe default must be present per case.
    problems += _missing_required_key_problems(rubric, cases)

    for c in rubric.criteria:
        # 1) Conditional JUDGE criterion with no applicability declared → it will
        #    be scored 0 on any benign case. Scoped to judge criteria on purpose:
        #    that is the defect this gate exists for (a judge grading an absent
        #    situation). Code checks are deterministic and default-filled — their
        #    missing-config failure mode is the suite-integrity gate's job (F3),
        #    not this one, so a code check_ref named like a reaction ("required_
        #    tool_called") is not a false positive here.
        if (c.scorer == "judge" and _looks_conditional(c)
                and not _has_applicability_declaration(c)):
            benign = [tc for tc in cases if not case_warrants_refusal(tc)]
            if benign:
                example = ", ".join(f"`{tc.test_id}`" for tc in benign[:3])
                problems.append(
                    f"criterion `{c.criterion_id}` reads as conditional (it grades "
                    f"a reaction to a situation) but declares no applicability; it "
                    f"will be scored 0 on benign case(s) {example} where the "
                    f"situation never arises. Declare `applies_when` (recorded N/A) "
                    f"or tag it `refusal`/`safety`.")

        # 2) Dead criterion: an applicability constraint that no case satisfies.
        if _has_applicability_declaration(c) and cases:
            applies_anywhere = any(
                c in applicable_to_case([c], tc) for tc in cases)
            if not applies_anywhere:
                problems.append(
                    f"criterion `{c.criterion_id}` declares an applicability "
                    f"constraint that matches no case — it is N/A on all "
                    f"{len(cases)} case(s) (a dead criterion). Widen the suite or "
                    f"remove the criterion.")

    return problems


def assert_suite_valid(rubric: Rubric, cases: list[TestCase]) -> None:
    """Raise :class:`SuiteValidationError` (naming every problem) if the suite
    would misapply a criterion. The gate that approval and the pre-run path call
    so a mismatched suite is impossible to run, not merely reported."""
    problems = validate_suite(rubric, cases)
    if problems:
        raise SuiteValidationError(
            "suite failed validation (SPEC-6 oracle gate):\n- "
            + "\n- ".join(problems))
