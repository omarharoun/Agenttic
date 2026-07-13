"""AssistantBench (Yoran et al. 2024) ingestion adapter.

AssistantBench is a realistic *web-agent* benchmark: time-consuming, open-ended
questions that require browsing the live web (e.g. "which gyms near Tompkins
Square Park have classes before 7am?"), with short factual gold answers that may
be a string, a number, a newline-separated list, or one JSON object per line.

This adapter maps each AssistantBench record into an agenttic ``TestCase`` whose
``expected['answer']`` carries the gold answer, scored by our two AssistantBench
canonical checks:

- ``answer_accuracy`` — fractional, partial-credit match of the agent's final
  answer to gold (token-F1 / numeric log-ratio / dict-F1; see metrics.answer_match).
- ``answer_attempted`` — did the agent answer or abstain (-> answer rate).

What this adapter does NOT reproduce: AssistantBench's live web environment and
the actual browsing trajectory. We score a candidate agent's *final answer*
against the real gold answer with AssistantBench's *own* fractional metric — we
do not reproduce the paper's leaderboard figures (those require live web runs).

Ships a vendored real sample of the dev/validation split (Apache-2.0; see
assistantbench_data/LICENSE + ATTRIBUTION.md). ``full=True`` fetches the whole
validation split (33 questions with gold answers) from HuggingFace; the test
split's answers are held out by the authors and are not scoreable offline.
"""

from __future__ import annotations

import json
import urllib.request
from importlib import resources

from ascore.metrics.datasets.base import DatasetAdapter, DatasetInfo
from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.testcase import TestCase

_HF = ("https://huggingface.co/datasets/AssistantBench/AssistantBench/"
       "resolve/main/assistant_bench_v1.0_dev.jsonl")
_DATA = resources.files("ascore.metrics.datasets") / "assistantbench_data"
_SAMPLE = "assistant_bench_v1.0_dev.sample.jsonl"

INFO = DatasetInfo(
    dataset_id="assistantbench",
    suite_id="assistantbench-v1",
    name="AssistantBench (real dataset)",
    citation="Yoran et al., AssistantBench: Can Web Agents Solve Realistic and "
             "Time-Consuming Tasks? (2024; arXiv:2407.15711).",
    license="Apache-2.0",
    source_url="https://huggingface.co/datasets/AssistantBench/AssistantBench",
)


def _jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _fetch(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "agenttic-ingest"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return _jsonl(r.read().decode())


class AssistantBenchAdapter(DatasetAdapter):
    info = INFO

    def _records(self, full: bool) -> list[dict]:
        if full:
            return _fetch(_HF)
        return _jsonl((_DATA / _SAMPLE).read_text())

    def load_records(self, *, full: bool = False) -> list[TestCase]:
        cases: list[TestCase] = []
        for rec in self._records(full):
            gold = (rec.get("answer") or "").strip()
            task = rec.get("task") or ""
            if not gold or not task:
                continue  # only keep questions with a gold answer (dev split)
            ab_id = rec.get("id", "")
            cases.append(TestCase(
                test_id=f"{self.info.suite_id}-{ab_id[:16]}",
                suite_id=self.info.suite_id, version=1,
                task_description=task[:200],
                input={"task": task, "assistantbench_id": ab_id,
                       "gold_url": rec.get("gold_url", "")},
                expected={"answer": gold, "abstain": False,
                          "source": "AssistantBench",
                          "difficulty": rec.get("difficulty", ""),
                          "assistantbench_id": ab_id},
                tags=["web_agent", "standard",
                      (rec.get("difficulty") or "").lower() or "unknown"],
                rubric_id=f"{self.info.suite_id}-rubric"))
        return cases

    def rubric(self) -> Rubric:
        rid = f"{self.info.suite_id}-rubric"
        checks = [
            ("answer_accuracy", "three_point",
             "Fractional answer accuracy vs gold (AssistantBench partial credit)"),
            ("answer_attempted", "binary",
             "Agent attempted an answer rather than abstaining (AssistantBench answer rate)"),
        ]
        return Rubric(rubric_id=rid, version=1, criteria=[
            Criterion(criterion_id=ref, description=desc, scorer="code",
                      scale=scale, check_ref=ref,
                      tags=["web_agent", "standard", "assistantbench"])
            for (ref, scale, desc) in checks])


def attribution_text() -> str:
    return (_DATA / "ATTRIBUTION.md").read_text()
