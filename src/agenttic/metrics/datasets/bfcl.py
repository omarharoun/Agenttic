"""BFCL (Berkeley Function-Calling Leaderboard) ingestion adapters.

Parses the real BFCL v3 splits (question + function schema + ground-truth call)
into agenttic TestCases scored by our tool-call-accuracy canonical checks,
preserving BFCL's expected-function-call ground truth in ``expected``. Ships a
vendored real sample per split (Apache-2.0; see bfcl_data/LICENSE +
ATTRIBUTION.md); ``full=True`` fetches the whole split from HuggingFace.

Splits covered (research roadmap #3 — tool-call accuracy across harder
multi-call/selection scenarios):

- ``simple`` — one function available, one call expected (``bfcl-simple-v3``).
- ``parallel`` — one function, several calls in one turn (``bfcl-parallel-v3``).
- ``multiple`` — several functions, select the right one (``bfcl-multiple-v3``).
- ``parallel_multiple`` — several functions, several calls
  (``bfcl-parallel-multiple-v3``).
- ``live_simple`` / ``live_multiple`` — real user-contributed prompts, single
  call / select-among-many (``bfcl-live-simple-v3`` / ``bfcl-live-multiple-v3``).

MULTI-CALL GROUND TRUTH. BFCL's ground truth is an ordered *list* of expected
calls ``[{func: {arg: [allowed...]}}, ...]`` — for parallel splits the same or
different functions are called several times in one turn. We preserve the full
ordered list so the canonical checks score every call:

- ``required_tools`` — the *set* of expected function names (selection accuracy:
  exactly these functions, no extras — handles select-among-many in ``multiple``).
- ``tool_sequence`` — the full *ordered list* of names incl. repeats, e.g.
  ``["spotify.play", "spotify.play"]`` (sequencing accuracy: every parallel call
  must be present, in order).
- ``tool_args`` — ``{func: {arg: [allowed...]}}`` (parameter accuracy vs BFCL's
  allowed-value lists). For a function called more than once in one parallel
  turn the per-call arg sets are merged by name; ``expected_calls`` retains the
  unmerged ordered ground truth for fidelity / future per-call scoring.
"""

from __future__ import annotations

import json
import urllib.request
from importlib import resources

from agenttic.metrics.datasets.base import DatasetAdapter, DatasetInfo
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import TestCase

_HF = ("https://huggingface.co/datasets/gorilla-llm/"
       "Berkeley-Function-Calling-Leaderboard/resolve/main/")
_DATA = resources.files("agenttic.metrics.datasets") / "bfcl_data"


def _jsonl(text: str) -> list[dict]:
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def _fetch(name: str) -> list[dict]:
    req = urllib.request.Request(_HF + name, headers={"User-Agent": "agenttic-ingest"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return _jsonl(r.read().decode())


class _BFCLSplitAdapter(DatasetAdapter):
    """Shared parsing for a single BFCL v3 split. Subclasses set ``split`` (the
    BFCL split name), ``info`` and ``case_tags``."""

    split: str = "simple"
    case_tags: list[str] = ["happy_path", "standard"]
    #: True when the split expects more than one call per turn (parallel*).
    multi_call: bool = False

    # -- record sourcing ----------------------------------------------------
    def _hf_names(self) -> tuple[str, str]:
        return (f"BFCL_v3_{self.split}.json",
                f"possible_answer/BFCL_v3_{self.split}.json")

    def _sample_names(self) -> tuple[str, str]:
        return (f"BFCL_v3_{self.split}.sample.json",
                f"BFCL_v3_{self.split}.sample.answers.json")

    def _records(self, full: bool):
        if full:
            q_name, a_name = self._hf_names()
            return _fetch(q_name), _fetch(a_name)
        q_name, a_name = self._sample_names()
        q = json.loads((_DATA / q_name).read_text())
        a = json.loads((_DATA / a_name).read_text())
        return q, a

    # -- parsing ------------------------------------------------------------
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
            if not seq:
                continue
            # full ordered ground truth, unmerged (one dict per expected call)
            expected_calls = [{"name": name, "args": args}
                              for entry in gt for name, args in entry.items()]
            # per-name arg map for the parameter check (merge repeats by name)
            tool_args: dict[str, dict] = {}
            for call in expected_calls:
                tool_args.setdefault(call["name"], {}).update(call["args"])
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
                          "expected_calls": expected_calls,
                          "abstain": False, "source": "BFCL",
                          "split": self.split, "bfcl_id": rec["id"]},
                tags=list(self.case_tags),
                rubric_id=f"{self.info.suite_id}-rubric"))
        return cases

    def rubric(self) -> Rubric:
        rid = f"{self.info.suite_id}-rubric"
        seq_desc = ("Correct multi-call set/sequence (BFCL parallel)"
                    if self.multi_call else "Correct call (BFCL single-call)")
        checks = [
            ("tool_selection_accuracy", "binary", "Correct function(s) selected (BFCL)"),
            ("tool_param_accuracy", "three_point", "Correct parameters vs BFCL ground truth"),
            ("tool_sequence_accuracy", "binary", seq_desc),
            ("abstention_correct", "binary", "Acts when a call is warranted (BFCL relevance)"),
        ]
        return Rubric(rubric_id=rid, version=1, criteria=[
            Criterion(criterion_id=ref, description=desc, scorer="code",
                      scale=scale, check_ref=ref, tags=["trajectory", "standard", "bfcl"])
            for (ref, scale, desc) in checks])


class BFCLAdapter(_BFCLSplitAdapter):
    """BFCL ``simple`` split — one function, one call (original suite)."""
    split = "simple"
    case_tags = ["happy_path", "standard"]
    info = DatasetInfo(
        dataset_id="bfcl",
        suite_id="bfcl-simple-v3",
        name="BFCL simple (real dataset)",
        citation="Patil et al., Berkeley Function-Calling Leaderboard (Gorilla, UC Berkeley).",
        license="Apache-2.0",
        source_url="https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard",
    )


class BFCLParallelAdapter(_BFCLSplitAdapter):
    """BFCL ``parallel`` split — one function, several calls in one turn."""
    split = "parallel"
    multi_call = True
    case_tags = ["parallel", "multi_call", "standard"]
    info = DatasetInfo(
        dataset_id="bfcl-parallel",
        suite_id="bfcl-parallel-v3",
        name="BFCL parallel (real dataset)",
        citation="Patil et al., Berkeley Function-Calling Leaderboard (Gorilla, UC Berkeley).",
        license="Apache-2.0",
        source_url="https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard",
    )


class BFCLMultipleAdapter(_BFCLSplitAdapter):
    """BFCL ``multiple`` split — several functions, select the right one."""
    split = "multiple"
    case_tags = ["multiple", "selection", "standard"]
    info = DatasetInfo(
        dataset_id="bfcl-multiple",
        suite_id="bfcl-multiple-v3",
        name="BFCL multiple (real dataset)",
        citation="Patil et al., Berkeley Function-Calling Leaderboard (Gorilla, UC Berkeley).",
        license="Apache-2.0",
        source_url="https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard",
    )


class BFCLParallelMultipleAdapter(_BFCLSplitAdapter):
    """BFCL ``parallel_multiple`` split — several functions, several calls."""
    split = "parallel_multiple"
    multi_call = True
    case_tags = ["parallel", "multiple", "multi_call", "standard"]
    info = DatasetInfo(
        dataset_id="bfcl-parallel-multiple",
        suite_id="bfcl-parallel-multiple-v3",
        name="BFCL parallel_multiple (real dataset)",
        citation="Patil et al., Berkeley Function-Calling Leaderboard (Gorilla, UC Berkeley).",
        license="Apache-2.0",
        source_url="https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard",
    )


class BFCLLiveSimpleAdapter(_BFCLSplitAdapter):
    """BFCL ``live_simple`` split — real user prompts, one function, one call."""
    split = "live_simple"
    case_tags = ["live", "happy_path", "standard"]
    info = DatasetInfo(
        dataset_id="bfcl-live-simple",
        suite_id="bfcl-live-simple-v3",
        name="BFCL live_simple (real dataset)",
        citation="Patil et al., Berkeley Function-Calling Leaderboard (Gorilla, UC Berkeley).",
        license="Apache-2.0",
        source_url="https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard",
    )


class BFCLLiveMultipleAdapter(_BFCLSplitAdapter):
    """BFCL ``live_multiple`` split — real user prompts, select-among-many."""
    split = "live_multiple"
    case_tags = ["live", "multiple", "selection", "standard"]
    info = DatasetInfo(
        dataset_id="bfcl-live-multiple",
        suite_id="bfcl-live-multiple-v3",
        name="BFCL live_multiple (real dataset)",
        citation="Patil et al., Berkeley Function-Calling Leaderboard (Gorilla, UC Berkeley).",
        license="Apache-2.0",
        source_url="https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard",
    )


# Adapters for the additional BFCL splits, keyed by dataset_id (the simple
# adapter stays registered in datasets/__init__.py alongside the other datasets).
BFCL_SPLIT_ADAPTERS = {
    a.info.dataset_id: a for a in (
        BFCLParallelAdapter, BFCLMultipleAdapter, BFCLParallelMultipleAdapter,
        BFCLLiveSimpleAdapter, BFCLLiveMultipleAdapter,
    )
}


def attribution_text() -> str:
    return (_DATA / "ATTRIBUTION.md").read_text()
