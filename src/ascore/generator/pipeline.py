"""Benchmark generator (Step 8) — staged LLM pipeline from a business
document to a draft test suite. Stages are separate calls with structured
output; the result is NEVER runnable until a human approves it.

Stages:
  1. extract_tasks(business_doc)       -> list of task specs
  2. define_criteria(task)             -> draft Rubric (anchored, narrow scales)
  3. generate_cases(task, ...)         -> test cases (happy/edge/adversarial mix)
  4. human gate: write review/{suite_id}.md; `ascore approve` flips the flag.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ascore.registry.sqlite_store import Registry
from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.testcase import TestCase, TestSuite
from ascore.scoring.checks import CHECKS

TAG_MIX = ("happy_path", "happy_path", "edge_case", "edge_case", "adversarial")

SYSTEM = (
    "You design rigorous evaluation benchmarks for AI agents. "
    "Respond with ONLY the requested JSON. No prose, no markdown fences."
)

EXTRACT_TASKS_PROMPT = """From the business document below, extract the discrete,
testable tasks an AI agent would perform. Return JSON:
{{"tasks": [{{"slug": "<a_z_underscores>", "name": "...", "description": "..."}}]}}

BUSINESS DOCUMENT:
{doc}"""

DEFINE_CRITERIA_PROMPT = """Design scoring criteria for this agent task. Return JSON:
{{"criteria": [{{"criterion_id": "...", "description": "...",
  "scorer": "code"|"judge", "scale": "binary"|"three_point",
  "check_ref": <one of {checks} — required for code, null for judge>,
  "anchors": {{"pass": "<concrete pass example>", "fail": "<concrete fail example>"}},
  "tags": []}}]}}
Rules: scales are binary or three_point ONLY. Judge criteria MUST have both anchors.
Code criteria MUST use a check_ref from the allowed list. Criteria about the
agent's process (tool usage, efficiency) must include the tag "trajectory".

TASK: {task}"""

GENERATE_CASES_PROMPT = """Write {n} test cases for this agent task: a mix of
happy-path, edge cases, and adversarial inputs (tag each). Return JSON:
{{"cases": [{{"task_description": "...", "input": {{...}},
  "expected": {{...}} or null, "tags": ["happy_path"|"edge_case"|"adversarial"]}}]}}
If a criterion uses final_output_matches_expected, expected MUST contain
"final_output". Mirror these criteria when writing expected values: {criteria}

TASK: {task}"""


class GeneratorError(RuntimeError):
    pass


class BenchmarkGenerator:
    def __init__(self, *, model: str, client=None, max_tokens: int = 4000):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    # -- LLM plumbing ------------------------------------------------------

    def _ask_json(self, prompt: str) -> dict:
        last = ""
        for _ in range(2):  # one retry on malformed output
            resp = self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens,
                system=SYSTEM, messages=[{"role": "user", "content": prompt}],
            )
            raw = "".join(b.text for b in resp.content
                          if getattr(b, "type", "") == "text").strip()
            raw = re.sub(r"^```(json)?|```$", "", raw).strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                last = f"{exc}: {raw[:200]!r}"
        raise GeneratorError(f"stage returned invalid JSON twice: {last}")

    # -- stages ------------------------------------------------------------

    def extract_tasks(self, business_doc: str) -> list[dict]:
        tasks = self._ask_json(EXTRACT_TASKS_PROMPT.format(doc=business_doc))["tasks"]
        if not tasks:
            raise GeneratorError("no tasks extracted from business document")
        return tasks

    def define_criteria(self, task: dict, rubric_id: str) -> Rubric:
        data = self._ask_json(DEFINE_CRITERIA_PROMPT.format(
            task=json.dumps(task), checks=sorted(CHECKS)))
        criteria = [Criterion(**c) for c in data["criteria"]]  # schema enforces anchors/scales
        return Rubric(rubric_id=rubric_id, version=1, criteria=criteria)

    def generate_cases(self, task: dict, *, suite_id: str, rubric: Rubric,
                       n: int) -> list[TestCase]:
        data = self._ask_json(GENERATE_CASES_PROMPT.format(
            n=n, task=json.dumps(task),
            criteria=json.dumps([c.model_dump() for c in rubric.criteria])))
        cases = []
        for i, c in enumerate(data["cases"]):
            cases.append(TestCase(
                test_id=f"{suite_id}-{task['slug']}-{i:03d}",
                suite_id=suite_id, version=1,
                task_description=c["task_description"],
                input=c["input"], expected=c.get("expected"),
                tags=c.get("tags") or [TAG_MIX[i % len(TAG_MIX)]],
                rubric_id=rubric.rubric_id,
            ))
        return cases

    # -- orchestration -----------------------------------------------------

    def generate_suite(
        self,
        business_doc: str,
        *,
        suite_id: str,
        registry: Registry,
        review_dir: str | Path = "review",
        cases_per_task: int = 5,
        on_progress=None,
    ) -> TestSuite:
        """Run all stages, persist the DRAFT suite (approved=False), and write
        the human-review file. The suite cannot run until `ascore approve`.
        ``on_progress(event_type, data)`` reports each LLM stage as it lands."""
        emit = on_progress or (lambda t, d: None)
        tasks = self.extract_tasks(business_doc)
        emit("tasks_extracted", {"total": len(tasks),
                                 "tasks": [t["slug"] for t in tasks]})
        all_cases: list[TestCase] = []
        rubrics: list[Rubric] = []
        for i, task in enumerate(tasks):
            rubric = self.define_criteria(task, rubric_id=f"{suite_id}-{task['slug']}")
            registry.save_rubric(rubric)
            rubrics.append(rubric)
            emit("criteria_defined", {"index": i, "total": len(tasks),
                                      "task": task["slug"],
                                      "n_criteria": len(rubric.criteria)})
            new_cases = self.generate_cases(task, suite_id=suite_id,
                                            rubric=rubric, n=cases_per_task)
            all_cases += new_cases
            emit("cases_generated", {"index": i, "total": len(tasks),
                                     "task": task["slug"],
                                     "n_cases": len(new_cases)})
        suite = TestSuite(
            suite_id=suite_id, version=1,
            business_context=business_doc[:500],
            test_ids=[c.test_id for c in all_cases],
            approved=False,
        )
        registry.save_suite(suite, all_cases)
        self._write_review(suite, tasks, rubrics, all_cases, Path(review_dir))
        return suite

    @staticmethod
    def _write_review(suite, tasks, rubrics, cases, review_dir: Path) -> None:
        review_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Review: suite `{suite.suite_id}` v{suite.version}",
            "",
            f"Status: **DRAFT — not runnable** until approved — UI: Resources → suites "
            f"→ approve, or CLI: `uv run ascore approve {suite.suite_id}`.",
            "",
            f"## Tasks ({len(tasks)})",
        ]
        for t in tasks:
            lines.append(f"- **{t['name']}** (`{t['slug']}`): {t['description']}")
        lines.append("\n## Criteria")
        for r in rubrics:
            lines.append(f"\n### Rubric `{r.rubric_id}`")
            for c in r.criteria:
                anchor = (f" — pass: \"{c.anchors.get('pass', '')}\" / "
                          f"fail: \"{c.anchors.get('fail', '')}\""
                          if c.scorer == "judge" else f" — check: `{c.check_ref}`")
                lines.append(f"- `{c.criterion_id}` [{c.scorer}/{c.scale}] "
                             f"{c.description}{anchor}")
        lines.append(f"\n## Sample cases ({len(cases)} total)")
        for c in cases[:10]:
            lines.append(f"- `{c.test_id}` [{', '.join(c.tags)}] "
                         f"input: `{json.dumps(c.input)[:120]}`")
        (review_dir / f"{suite.suite_id}.md").write_text("\n".join(lines))
