"""τ-bench / τ²-bench import (SPEC-7 Step 33).

Extends the Harbor import pattern to conversational, stateful tasks: a domain DB
becomes an ``Environment.seed_state``, APIs become ToolSpecs, the policy becomes
``policy_doc``, the user instruction becomes a ``UserScenario``, the goal DB
state becomes ``goal_state`` and required communications become ``must_convey``.

Honesty rules (as Harbor): the license is verified and attribution preserved at
import time — import REFUSES when a license cannot be determined. τ² dual-control
(user-side tool calls) is OUT of scope until the user sim can call tools; those
tasks are listed ``unsupported`` with a reason, never approximated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agenttic.schema.environment import Environment, ToolSpec
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import OracleSolution, TestCase, TestSuite
from agenttic.schema.user_scenario import UserScenario


class LicenseUndeterminedError(RuntimeError):
    """Import refuses: a task's redistribution license could not be determined."""


@dataclass
class TauImportResult:
    suite_id: str
    version: int
    supported_case_ids: list[str] = field(default_factory=list)
    unsupported_tasks: list[dict] = field(default_factory=list)   # {task_id, reasons}
    license: str | None = None
    attribution: str | None = None


def _env_from_task(task: dict, env_id: str) -> Environment:
    tools = [ToolSpec(name=t["name"], effect=t.get("effect", "read"),
                      op=t.get("op", "get"), entity_type=t["entity_type"],
                      input_schema=t.get("input_schema", {}))
             for t in task.get("tools", [])]
    return Environment(env_id=env_id, version=1,
                       seed_state=task.get("domain_db", {}), tools=tools)


def _scenario(task: dict) -> UserScenario:
    ui = task.get("user_instruction") or {}
    return UserScenario(persona=ui.get("persona", ""),
                        goal=ui.get("goal", task.get("instruction", "")),
                        known_facts=ui.get("known_facts", {}),
                        hidden_facts=ui.get("hidden_facts", {}),
                        temperament=ui.get("temperament", "neutral"),
                        max_turns=int(ui.get("max_turns", 8)))


def import_tau(reg, tasks: list[dict], *, suite_id: str, name: str,
               version: str = "1", default_license: str | None = None) -> TauImportResult:
    origin = f"tau:{name}@{version}"
    license_ = default_license or next((t.get("license") for t in tasks if t.get("license")), None)
    if not license_:
        raise LicenseUndeterminedError(
            f"cannot import {origin}: no license on any task and no default_license "
            "given — redistribution requires a determinable license (SPEC-7 33)")
    attribution = next((t.get("attribution") for t in tasks if t.get("attribution")), None)
    canary = next((t["canary"] for t in tasks if t.get("canary")), None)
    result = TauImportResult(suite_id=suite_id, version=1, license=license_,
                             attribution=attribution)
    cases: list[TestCase] = []

    for task in tasks:
        tid = str(task.get("task_id") or f"task{len(cases)}")
        if task.get("dual_control"):
            result.unsupported_tasks.append({"task_id": tid, "reasons": [
                "τ² dual-control (user-side tool calls) — out of scope until the "
                "user simulator can call tools; not approximated"]})
            continue
        if not task.get("goal_db_state"):
            result.unsupported_tasks.append({"task_id": tid, "reasons": [
                "no goal DB state — nothing to verify deterministically"]})
            continue

        env_id = f"{suite_id}-{tid}-env"
        reg.save_environment(_env_from_task(task, env_id))

        criteria = [Criterion(criterion_id="goal", description="reaches goal state",
                              scorer="code", scale="binary", check_ref="end_state_matches_goal")]
        expected: dict = {"goal_state": task["goal_db_state"]}
        if task.get("required_communications"):
            criteria.append(Criterion(criterion_id="conveyed", description="conveys required info",
                                      scorer="code", scale="binary",
                                      check_ref="required_info_conveyed", tags=["policy"]))
            expected["must_convey"] = list(task["required_communications"])
        if task.get("policy"):
            criteria.append(Criterion(criterion_id="confirm", description="confirms before writing",
                                      scorer="code", scale="binary",
                                      check_ref="confirmation_before_write", tags=["policy"]))
        rubric_id = f"{suite_id}-{tid}"
        reg.save_rubric(Rubric(rubric_id=rubric_id, version=1, criteria=criteria,
                               weights={c.criterion_id: 1.0 for c in criteria}))

        oracle_calls = task.get("oracle_tool_calls")
        cases.append(TestCase(
            test_id=f"{suite_id}-{tid}", suite_id=suite_id, version=1,
            task_description=str(task.get("instruction", "")),
            input={"instruction": task.get("instruction", "")},
            expected=expected, tags=["tau"], rubric_id=rubric_id, env_id=env_id,
            user_scenario=_scenario(task),
            oracle=OracleSolution(final_output=str(task.get("goal_final_output", "")),
                                  tool_calls=oracle_calls, authored_by="human") if oracle_calls else None))
        result.supported_case_ids.append(cases[-1].test_id)

    suite = TestSuite(suite_id=suite_id, version=1,
                      business_context=f"Imported from {origin}",
                      test_ids=[c.test_id for c in cases], approved=False,
                      policy_doc=next((t.get("policy") for t in tasks if t.get("policy")), None),
                      origin=origin, canary=canary, license=license_, attribution=attribution)
    reg.save_suite(suite, cases)
    return result
