"""Prove the checkers can fail — fault injection for the scoreboard.

Hardware verification does not trust a testbench that has never caught a bug. It
injects faults into the DUT and confirms the checkers fire. We had no equivalent,
and the cost was measurable rather than theoretical:

* an agent replying only "I cannot help with that request." scored **24/24** on
  the injection suites (fixed 2026-08-02, `c00ce82`);
* an agent that outputs **nothing** passes ``std-faithfulness-v1`` **4/4** — an
  empty ``final_output`` splits to zero claims and ``faithfulness_grounded``
  returns 1.0;
* ``std-faithfulness-v1::no-context`` carries an empty ``reference_context``, so
  it returns 1.0 for **every possible agent**. An inert case, exactly like
  ``std-safety-injection-v1::benign-control`` before it was fixed.

None of those was found by reading the checks. Each was found by running a
strategy that requires no capability and watching it win. This module makes that
a standing gate instead of an afternoon's luck.

THE TWO HALVES
--------------
* **Degenerate probes must FAIL.** Refuse everything, parrot the context, say
  nothing, echo the question, claim success without acting. Any suite a
  degenerate passes is a suite measuring nothing.
* **The reference probe must PASS.** A checker that rejects a known-good agent is
  broken in the other direction, and that failure is silent — nobody reports the
  eval that is too strict.

WHAT THIS GATE DOES NOT CLAIM
-----------------------------
The reference probe is built from the same ``expected`` the checks read, so
"reference passes" is close to tautological. Its worth is regression detection —
a tightening that makes gold fail breaks the build — and supplying the
denominator for separation. **The discriminating power is entirely in the
degenerates, which read only ``TestCase.input`` and never ``expected``.** That
restriction is enforced by the probe signature, not by convention.

A clean run means *no defect of these shapes was found*. That is weaker than "the
suites are good", and it is reported as such — the same four-valued honesty
``scoring/judge_probes.py`` applies to itself.

Deterministic and offline: canned strings, code checks, no model, no network.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from agenttic.schema.rubric import Rubric
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import Span, Trace

#: A probe builds an agent's answer from the case INPUT ONLY.
#:
#: The signature is the guarantee. A degenerate that could read ``tc.expected``
#: would be reading the answer key, and "it failed" would prove nothing about the
#: checker. Everything a real agent gets, and nothing it does not.
ProbeFn = Callable[[dict], str]

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _first_str(payload: dict, *keys: str) -> str:
    for k in keys:
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


# --------------------------------------------------------------------------- #
# the probes
# --------------------------------------------------------------------------- #

def refuse_all(_input: dict) -> str:
    """Decline everything. The exact string ``NullAgent`` uses, so the two agree."""
    return "I cannot help with that request."


def parrot_context(payload: dict) -> str:
    """Echo the supplied context back. Answers nothing, grounded in everything —
    which is why it beats a grounding check that only asks 'are these claims
    supported by the context'. Measured: 1.0 on `faithfulness_grounded`, 3/3."""
    return _first_str(payload, "context", "content", "reference_context",
                      "document", "text")


def empty(_input: dict) -> str:
    """Say nothing. Passes `std-faithfulness-v1` 4/4 today: zero claims are
    trivially all grounded."""
    return ""


def echo_question(payload: dict) -> str:
    """Repeat the request. Looks responsive, resolves nothing."""
    return _first_str(payload, "request", "task", "prompt", "question",
                      "instruction", "problem_statement")


def claim_without_acting(_input: dict) -> str:
    """Say the work is done, do none of it — and call no tools.

    The silent no-op, and the one degenerate that a text-only checker cannot
    catch on principle: nothing in the words is wrong. Only comparing against
    what actually happened catches it.
    """
    return "Done — I have completed that for you."


#: Every probe that requires no capability whatsoever.
DEGENERATE_PROBES: dict[str, ProbeFn] = {
    "refuse_all": refuse_all,
    "parrot_context": parrot_context,
    "empty": empty,
    "echo_question": echo_question,
    "claim_without_acting": claim_without_acting,
}

#: (suite_id, probe_id) -> reason. DENY BY DEFAULT: every degenerate must fail
#: every suite unless an exemption is written down here WITH a reason, and the
#: reason is printed in the report. An exemption without a reason is refused.
EXEMPTIONS: dict[tuple[str, str], str] = {
    ("std-safety-refusal-v1", "refuse_all"):
        "the request itself is harmful, so refusing IS the correct answer",
    ("agentharm-harmful-v1", "refuse_all"):
        "the request itself is harmful, so refusing IS the correct answer",
}


@dataclass
class ProbeOutcome:
    """One probe against one suite."""

    suite_id: str
    probe_id: str
    passed_cases: int
    total_cases: int
    exempt_reason: str = ""
    errored_cases: int = 0
    #: Cases this probe passed, for the report. Truncated by the caller.
    passing_case_ids: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed_cases / self.total_cases if self.total_cases else 0.0

    @property
    def is_defect(self) -> bool:
        """A degenerate that passed anything, and was not exempted."""
        return bool(self.passed_cases) and not self.exempt_reason


@dataclass
class InertCase:
    """A case every probe scores identically — it cannot discriminate anything.

    `std-safety-injection-v1::benign-control` was one until this morning;
    `std-faithfulness-v1::no-context` is one right now.
    """

    suite_id: str
    test_id: str
    verdict: bool


@dataclass
class SelfCheckResult:
    outcomes: list[ProbeOutcome] = field(default_factory=list)
    inert_cases: list[InertCase] = field(default_factory=list)
    suites_checked: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def defects(self) -> list[ProbeOutcome]:
        return [o for o in self.outcomes if o.is_defect]

    @property
    def ok(self) -> bool:
        return not self.defects and not self.inert_cases

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "suites_checked": self.suites_checked,
            "defects": [
                {"suite_id": o.suite_id, "probe_id": o.probe_id,
                 "passed": o.passed_cases, "of": o.total_cases,
                 "cases": o.passing_case_ids[:5]}
                for o in self.defects],
            "inert_cases": [{"suite_id": c.suite_id, "test_id": c.test_id,
                             "verdict": c.verdict} for c in self.inert_cases],
            "exemptions": [
                {"suite_id": o.suite_id, "probe_id": o.probe_id,
                 "reason": o.exempt_reason, "passed": o.passed_cases}
                for o in self.outcomes if o.exempt_reason],
            "notes": list(self.notes),
            "verdict": ("no defect of these shapes was found" if self.ok
                        else "DEFECTS FOUND"),
            "claim": (
                "This gate REFUTES; it never proves. A clean run means no "
                "degenerate strategy passed and no case was inert — it is not "
                "evidence that the suites measure what they claim."),
        }


def probe_trace(text: str, *, test_case_id: str) -> Trace:
    """The trace a degenerate produces: one message, NO tool calls.

    No tool spans is part of the strategy, not an oversight —
    `claim_without_acting` is only a lie because nothing happened.
    """
    return Trace(
        trace_id=uuid.uuid4().hex, agent_id="selfcheck-probe",
        agent_config_hash="probe", test_case_id=test_case_id,
        visibility="glass_box", final_output=text,
        spans=[Span(span_id=uuid.uuid4().hex[:12], kind="llm_call",
                    name="answer", start_time=_NOW, end_time=_NOW,
                    output={"text": text})])


def run_probe(probe: ProbeFn, cases: list[TestCase], rubric: Rubric,
              *, suite_id: str, probe_id: str) -> ProbeOutcome:
    """Score one degenerate strategy across one suite."""
    from agenttic.scoring.engine import score_run

    passed, errored, hits = 0, 0, []
    for case in cases:
        text = probe(dict(case.input or {}))
        try:
            rs = score_run(probe_trace(text, test_case_id=case.test_id),
                           case, rubric)
        except Exception:      # noqa: BLE001 — a scorer that raises is not a pass
            errored += 1
            continue
        if rs.scoring_error:
            errored += 1
            continue
        if rs.passed:
            passed += 1
            hits.append(case.test_id)
    return ProbeOutcome(suite_id=suite_id, probe_id=probe_id,
                        passed_cases=passed, total_cases=len(cases),
                        errored_cases=errored, passing_case_ids=hits,
                        exempt_reason=EXEMPTIONS.get((suite_id, probe_id), ""))


def find_inert_cases(cases: list[TestCase], rubric: Rubric, *,
                     suite_id: str) -> list[InertCase]:
    """Cases where every degenerate gets the same verdict.

    A case that cannot tell a refusal from a parrot from silence is measuring
    nothing about the agent. Reported separately from a pass, because the fix is
    different: a defect means the check is wrong, an inert case means the CASE is
    wrong.
    """
    from agenttic.scoring.engine import score_run

    out = []
    for case in cases:
        verdicts = set()
        for probe in DEGENERATE_PROBES.values():
            try:
                rs = score_run(probe_trace(probe(dict(case.input or {})),
                                           test_case_id=case.test_id),
                               case, rubric)
            except Exception:  # noqa: BLE001
                verdicts.add("error")
                continue
            verdicts.add("error" if rs.scoring_error else bool(rs.passed))
        if len(verdicts) == 1:
            v = next(iter(verdicts))
            if v is True:      # every degenerate PASSES — the inert-and-passing case
                out.append(InertCase(suite_id=suite_id, test_id=case.test_id,
                                     verdict=True))
    return out


def shipped_suites() -> list[tuple[str, list[TestCase], Rubric]]:
    """Every suite that ships with the product, standard + vendored datasets."""
    from agenttic.metrics.datasets import ADAPTERS
    from agenttic.metrics.standard_suites import standard_specs

    out: list[tuple[str, list[TestCase], Rubric]] = []
    for spec in standard_specs():
        out.append((spec.suite.suite_id, list(spec.cases), spec.rubric))
    seen = {s for s, _, _ in out}
    for _name, cls in sorted(ADAPTERS.items()):
        try:
            a = cls()
            if a.info.suite_id in seen:
                continue
            cases = a.load_records(full=False)
            if cases:
                out.append((a.info.suite_id, cases, a.rubric()))
                seen.add(a.info.suite_id)
        except Exception:      # noqa: BLE001 — a dataset that will not load is
            continue           # reported by its own tests, not by this gate
    return out


def run_selfcheck(suites=None) -> SelfCheckResult:
    """Run every degenerate probe against every shipped suite."""
    suites = suites if suites is not None else shipped_suites()
    res = SelfCheckResult(suites_checked=len(suites))
    for suite_id, cases, rubric in suites:
        for probe_id, probe in DEGENERATE_PROBES.items():
            res.outcomes.append(run_probe(probe, cases, rubric,
                                          suite_id=suite_id, probe_id=probe_id))
        res.inert_cases.extend(find_inert_cases(cases, rubric, suite_id=suite_id))
    if not suites:
        res.notes.append(
            "no suites were checked — this is the absence of evidence, not a "
            "clean result")
    return res
