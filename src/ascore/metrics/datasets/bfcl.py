"""BFCL (Berkeley Function-Calling Leaderboard) ingestion adapter.

Parses the real BFCL ``simple`` split (question + function schema + ground-truth
call) into agenttic TestCases scored by our tool-call-accuracy canonical checks,
preserving BFCL's expected-function-call ground truth in ``expected``. Ships a
vendored 25-record real sample (Apache-2.0; see bfcl_data/LICENSE +
ATTRIBUTION.md); ``full=True`` fetches the whole split from HuggingFace.
"""

from __future__ import annotations

import json
import urllib.request
from importlib import resources

from ascore.metrics.datasets.base import DatasetAdapter, DatasetInfo
from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.testcase import TestCase

_HF = ("https://huggingface.co/datasets/gorilla-llm/"
       "Berkeley-Function-Calling-Leaderboard/resolve/main/")
_DATA = resources.files("ascore.metrics.datasets") / "bfcl_data"

INFO = DatasetInfo(
    dataset_id="bfcl",
    suite_id="bfcl-simple-v3",
    name="BFCL simple (real dataset)",
    citation="Patil et al., Berkeley Function-Calling Leaderboard (Gorilla, UC Berkeley).",
    license="Apache-2.0",
    source_url="https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard",
)


def _jsonl(text: str) -> list[dict]:
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def _fetch(name: str) -> list[dict]:
    req = urllib.request.Request(_HF + name, headers={"User-Agent": "agenttic-ingest"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return _jsonl(r.read().decode())


class BFCLAdapter(DatasetAdapter):
    info = INFO

    def _records(self, full: bool):
        if full:
            return _fetch("BFCL_v3_simple.json"), _fetch("possible_answer/BFCL_v3_simple.json")
        q = json.loads((_DATA / "BFCL_v3_simple.sample.json").read_text())
        a = json.loads((_DATA / "BFCL_v3_simple.sample.answers.json").read_text())
        return q, a

    def load_records(self, *, full: bool = False) -> list[TestCase]:
        questions, answers = self._records(full)
        ans_by_id = {a["id"]: a for a in answers}
        cases: list[TestCase] = []
        for rec in questions:
            ans = ans_by_id.get(rec["id"])
            if not ans:
                continue  # only keep cases we have ground truth for
            gt = ans["ground_truth"]  # [{func: {arg: [allowed...]}}, ...]
            seq = [name for entry in gt for name in entry]
            tool_args = {name: args for entry in gt for name, args in entry.items()}
            # first user turn text
            turns = rec.get("question") or [[{}]]
            content = next((m.get("content", "") for m in turns[0]
                            if m.get("role") == "user"), turns[0][0].get("content", ""))
            cases.append(TestCase(
                test_id=f"{self.info.suite_id}-{rec['id']}",
                suite_id=self.info.suite_id, version=1,
                task_description=content[:200],
                input={"question": content, "functions": rec.get("function", []),
                       "bfcl_id": rec["id"]},
                expected={"required_tools": list(dict.fromkeys(seq)),
                          "tool_sequence": seq, "tool_args": tool_args,
                          "abstain": False, "source": "BFCL", "bfcl_id": rec["id"]},
                tags=["happy_path", "standard"],
                rubric_id=f"{self.info.suite_id}-rubric"))
        return cases

    def rubric(self) -> Rubric:
        rid = f"{self.info.suite_id}-rubric"
        checks = [
            ("tool_selection_accuracy", "binary", "Correct function selected (BFCL)"),
            ("tool_param_accuracy", "three_point", "Correct parameters vs BFCL ground truth"),
            ("tool_sequence_accuracy", "binary", "Correct call (BFCL single-call)"),
            ("abstention_correct", "binary", "Acts when a call is warranted (BFCL relevance)"),
        ]
        return Rubric(rubric_id=rid, version=1, criteria=[
            Criterion(criterion_id=ref, description=desc, scorer="code",
                      scale=scale, check_ref=ref, tags=["trajectory", "standard", "bfcl"])
            for (ref, scale, desc) in checks])


def attribution_text() -> str:
    return (_DATA / "ATTRIBUTION.md").read_text()
