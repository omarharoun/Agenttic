"""Harbor import (SPEC-6 Step 27) — bring public benchmark tasks into our schema.

A Harbor task carries an instruction, an environment reference, a set of tests,
an oracle solution, and a time limit. We map what our *check registry can
actually verify* (I/O-verifiable outcome checks) and are HONEST about the rest:
a test we cannot express is recorded as ``unsupported`` with a reason, never
silently mangled into a check that means something else. A task with no
supported test becomes an unsupported task, listed — not a broken case.

Environment provisioning (per-task Docker) is explicitly OUT of scope: this
imports benchmarks whose verification maps to our outcome-check model. Imported
suites enter as DRAFTS (approved=False), carry ``origin="harbor:<name>@<ver>"``,
preserve any canary string byte-for-byte, and go through the SAME integrity
gates and approve flow as any suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import OracleSolution, TestCase, TestSuite

# Harbor test "kind" -> (our check_ref, builder(test) -> expected-fields). Only
# kinds whose verification our check registry expresses exactly appear here;
# everything else is reported unsupported.
_SUPPORTED = {
    "output_equals": ("final_output_matches_expected",
                      lambda t: {"final_output": str(t["value"])}),
    "output_matches": ("final_output_matches_expected",
                       lambda t: {"final_output": str(t["value"])}),
    "valid_json": ("valid_json_output", lambda t: {}),
    "tool_called": ("required_tool_called",
                    lambda t: {"required_tools": [t["tool"]] if "tool" in t else list(t.get("tools", []))}),
    "forbidden_tool": ("forbidden_tool_not_called",
                       lambda t: {"forbidden_tools": [t["tool"]] if "tool" in t else list(t.get("tools", []))}),
    "max_steps": ("steps_under_limit", lambda t: {"max_steps": int(t["value"])}),
}

# Why a kind can't be imported (needs an execution environment we don't provision).
_UNSUPPORTED_REASON = {
    "file_exists": "filesystem assertion — needs container provisioning (out of scope)",
    "exit_code": "process exit code — needs container execution (out of scope)",
    "pytest": "runs a test suite in a container — needs provisioning (out of scope)",
    "command_output": "shell command output — needs container execution (out of scope)",
    "output_contains": "substring assertion not in our outcome-check registry",
}


@dataclass
class HarborImportResult:
    suite_id: str
    version: int
    supported_case_ids: list[str] = field(default_factory=list)
    #: partially-imported cases: {test_id: [reasons for skipped checks]}
    partial_checks: dict = field(default_factory=dict)
    #: tasks with NO supported test — listed, not imported
    unsupported_tasks: list[dict] = field(default_factory=list)
    canary: str | None = None


def _reason(kind: str) -> str:
    return _UNSUPPORTED_REASON.get(kind, f"unknown Harbor test kind {kind!r}")


def import_harbor(reg, tasks: list[dict], *, suite_id: str, name: str,
                  version: str = "1", business_context: str = "") -> HarborImportResult:
    """Import Harbor tasks into a DRAFT suite. Returns what imported and what
    could not, with reasons."""
    origin = f"harbor:{name}@{version}"
    canary = next((t["canary"] for t in tasks if t.get("canary")), None)
    result = HarborImportResult(suite_id=suite_id, version=1, canary=canary)
    cases: list[TestCase] = []

    for task in tasks:
        tid = str(task.get("task_id") or task.get("id") or f"task{len(cases)}")
        criteria: list[Criterion] = []
        expected: dict = {}
        skipped: list[str] = []
        for i, test in enumerate(task.get("tests", [])):
            kind = test.get("kind") or test.get("type")
            if kind in _SUPPORTED:
                check_ref, build = _SUPPORTED[kind]
                cid = f"{kind}_{i}"
                criteria.append(Criterion(criterion_id=cid, description=f"Harbor {kind}",
                                          scorer="code", scale="binary", check_ref=check_ref))
                expected.update(build(test))
            else:
                skipped.append(f"{kind}: {_reason(kind)}")

        if not criteria:
            # nothing we can verify → don't fabricate a case
            result.unsupported_tasks.append({"task_id": tid, "reasons": skipped})
            continue

        rubric_id = f"{suite_id}-{tid}"
        reg.save_rubric(Rubric(rubric_id=rubric_id, version=1, criteria=criteria,
                               weights={c.criterion_id: 1.0 for c in criteria}))
        oracle = task.get("oracle")
        case = TestCase(
            test_id=f"{suite_id}-{tid}", suite_id=suite_id, version=1,
            task_description=str(task.get("instruction", "")),
            input={"instruction": task.get("instruction", ""), **(task.get("input") or {})},
            expected=expected or None, tags=["harbor"], rubric_id=rubric_id,
            oracle=OracleSolution(
                final_output=str(oracle.get("final_output", "")),
                tool_sequence=list(oracle["tool_sequence"]) if oracle and oracle.get("tool_sequence") else None,
                authored_by="human") if oracle else None,
            timeout_sec=float(task["time_limit_sec"]) if task.get("time_limit_sec") else None,
        )
        cases.append(case)
        result.supported_case_ids.append(case.test_id)
        if skipped:
            result.partial_checks[case.test_id] = skipped

    suite = TestSuite(
        suite_id=suite_id, version=1,
        business_context=business_context or f"Imported from {origin}",
        test_ids=[c.test_id for c in cases], approved=False,   # DRAFT — same flow
        origin=origin, canary=canary)
    reg.save_suite(suite, cases)
    return result
