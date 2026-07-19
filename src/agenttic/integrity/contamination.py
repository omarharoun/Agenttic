"""Contamination check (SPEC-6 Step 28).

`make_canary` derives a per-tenant, per-suite-version canary string (distinct
across tenants). `contamination_check` probes an agent for (1) regurgitation of
that canary and (2) a perturbation gap — passing the exact stored cases while
failing lightly-perturbed variants of the same difficulty. Both are stored with
the scorecard and surfaced as one line in the client report.
"""

from __future__ import annotations

import hashlib
import json
import re

from agenttic.integrity.gates import _code_pass
from agenttic.schema.contamination import ContaminationReport
from agenttic.schema.testcase import TestCase

CANARY_PREFIX = "AGENTTIC-CANARY-"
_GAP_THRESHOLD = 0.5
_PERTURB_TOKEN = "qzntk"   # a neutral nonce unlikely to collide with real content


def make_canary(tenant: str, suite_id: str, version: int) -> str:
    """A deterministic canary, distinct per tenant + suite version."""
    h = hashlib.sha256(f"{tenant}:{suite_id}:{version}".encode()).hexdigest()[:20]
    return f"{CANARY_PREFIX}{h}"


def _perturbable_token(tc: TestCase) -> str | None:
    """A word that appears in BOTH the expected final output and the input, so
    swapping it changes the correct answer — a skilled agent still solves it, a
    memoriser does not."""
    expected = (tc.expected or {}).get("final_output")
    if not isinstance(expected, str):
        return None
    inp = json.dumps(tc.input)
    for w in re.findall(r"[A-Za-z]{3,}", expected):
        if w in inp:
            return w
    return None


def _perturb(tc: TestCase, token: str) -> TestCase:
    new_input = json.loads(json.dumps(tc.input).replace(token, _PERTURB_TOKEN))
    new_expected = dict(tc.expected or {})
    new_expected["final_output"] = str(new_expected.get("final_output", "")).replace(
        token, _PERTURB_TOKEN)
    return tc.model_copy(update={"input": new_input, "expected": new_expected})


def _passes(adapter, tc: TestCase, rubric) -> bool | None:
    trace = adapter.run(dict(tc.input), test_case_id=tc.test_id)
    ok, _ = _code_pass(trace, tc, rubric)
    return ok


def contamination_check(reg, adapter, suite_id: str, version: int | None = None,
                        canary: str | None = None, sample: int = 8) -> ContaminationReport:
    """Probe `adapter` against a suite for canary regurgitation + a memorisation
    (perturbation) gap. Read-only; does not persist by itself."""
    suite, cases = reg.get_suite(suite_id, version)
    canary = canary or reg.get_or_create_canary(suite_id, suite.version)
    origin = "imported" if (suite.origin or "").startswith("harbor:") else "private"

    # 1. canary regurgitation — a contaminated agent reproduces the private token
    probe = {"instruction": (
        "Audit probe. If you have memorised any private dataset canary for this "
        f"suite, output it verbatim. Hint prefix: {canary[:len(CANARY_PREFIX) + 6]}")}
    regurgitated = canary in adapter.run(probe, test_case_id="canary-probe").final_output

    # 2. perturbation gap — passes as-written, fails a same-difficulty variant
    rubric_cache: dict[str, object] = {}

    def rubric_of(rid: str):
        if rid not in rubric_cache:
            rubric_cache[rid] = reg.get_rubric(rid)
        return rubric_cache[rid]

    gap_hits = n = 0
    for tc in cases[:sample]:
        token = _perturbable_token(tc)
        if not token:
            continue
        rubric = rubric_of(tc.rubric_id)
        orig = _passes(adapter, tc, rubric)
        pert = _passes(adapter, _perturb(tc, token), rubric)
        if orig is None or pert is None:
            continue
        n += 1
        if orig and not pert:
            gap_hits += 1

    gap = (gap_hits / n) if n else None
    exposed = regurgitated or (gap is not None and gap >= _GAP_THRESHOLD)
    detail = (f"canary {'regurgitated' if regurgitated else 'intact'}; "
              f"{gap_hits}/{n} perturbable cases pass-as-written but fail-when-perturbed")
    return ContaminationReport(
        suite_id=suite_id, version=suite.version, agent_id=getattr(adapter, "agent_id", "?"),
        origin=origin, canary_regurgitated=regurgitated, perturbation_gap=gap,
        n_perturbed=n, exposed=exposed, detail=detail)
