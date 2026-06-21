"""τ-bench (tau-bench, Sierra) ingestion adapter.

τ-bench evaluates *tool-agent-user* interaction in realistic customer-service
domains (retail, airline): an LLM user-simulator converses with the agent, which
must call domain tools against a stateful database, and the official reward
compares the resulting database state (and required outputs) to a human-annotated
ground-truth trajectory of write actions.

What this adapter maps (the tractable, deterministic part): each τ-bench task's
human-annotated ground-truth ``actions`` — the ordered tool calls with their
arguments — into agenttic ``TestCase``s scored by our tool-call-accuracy /
sequence canonical checks (the same scorers behind BFCL). A candidate agent's
tool-call trajectory is scored against this ground truth.

What this adapter does NOT reproduce (and does not claim to): τ-bench's LLM
user-simulator, the stateful retail/airline databases, the multi-turn
conversation dynamics, and the official database-state-hash reward function.
We score an annotated *trajectory* against the annotated ground-truth trajectory,
exactly as we treat BFCL — see ATTRIBUTION.md.

Ships a vendored real sample (MIT; see tau_bench_data/LICENSE + ATTRIBUTION.md)
derived by ``_parse_task_module`` from the upstream task files; ``full=True``
fetches and parses the full retail+airline *test* splits from GitHub.
"""

from __future__ import annotations

import ast
import json
import urllib.request
from importlib import resources

from ascore.metrics.datasets.base import DatasetAdapter, DatasetInfo
from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.testcase import TestCase

_GH = ("https://raw.githubusercontent.com/sierra-research/tau-bench/main/"
       "tau_bench/envs/")
# (domain, upstream task file) — the public test splits.
_FILES = [("retail", "retail/tasks_test.py"), ("airline", "airline/tasks_test.py")]
_DATA = resources.files("ascore.metrics.datasets") / "tau_bench_data"

INFO = DatasetInfo(
    dataset_id="tau-bench",
    suite_id="tau-bench-v1",
    name="τ-bench (real dataset)",
    citation="Yao et al., τ-bench: A Benchmark for Tool-Agent-User Interaction "
             "in Real-World Domains (Sierra, 2024; arXiv:2406.12045).",
    license="MIT",
    source_url="https://github.com/sierra-research/tau-bench",
)


# -- parsing the upstream task modules -------------------------------------
# The task files are Python literals: a module-level ``TASKS[_TEST]`` list of
# ``Task(annotator=..., user_id=..., instruction=..., actions=[Action(...)])``.
# We parse the AST (no import of tau_bench, no code execution) into plain dicts.

def _literal(node):
    if isinstance(node, ast.Call):                       # Action(...) / nested
        return {kw.arg: _literal(kw.value) for kw in node.keywords}
    if isinstance(node, ast.List):
        return [_literal(e) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return [_literal(e) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {_literal(k): _literal(v) for k, v in zip(node.keys, node.values)}
    return ast.literal_eval(node)


def _parse_task_module(source: str, domain: str) -> list[dict]:
    """Parse a τ-bench ``tasks_*.py`` source into normalized task records."""
    tree = ast.parse(source)
    tasks_node = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            name = getattr(node.targets[0], "id", "")
            if name.startswith("TASKS"):
                tasks_node = node.value
                break
    if tasks_node is None:
        return []
    out: list[dict] = []
    for i, elt in enumerate(tasks_node.elts):
        if not isinstance(elt, ast.Call):
            continue
        fields = {kw.arg: kw.value for kw in elt.keywords}
        actions = [{"name": a["name"], "kwargs": a.get("kwargs", {})}
                   for a in _literal(fields["actions"])] if "actions" in fields else []
        out.append({
            "domain": domain,
            "task_index": i,
            "annotator": ast.literal_eval(fields["annotator"]) if "annotator" in fields else "",
            "user_id": ast.literal_eval(fields["user_id"]) if "user_id" in fields else "",
            "instruction": ast.literal_eval(fields["instruction"]) if "instruction" in fields else "",
            "actions": actions,
        })
    return out


def _fetch(path: str) -> str:
    req = urllib.request.Request(_GH + path, headers={"User-Agent": "agenttic-ingest"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()


class TauBenchAdapter(DatasetAdapter):
    info = INFO

    def _records(self, full: bool) -> list[dict]:
        if full:
            recs: list[dict] = []
            for domain, path in _FILES:
                recs.extend(_parse_task_module(_fetch(path), domain))
            return recs
        return json.loads((_DATA / "tau_bench.sample.json").read_text())

    def load_records(self, *, full: bool = False) -> list[TestCase]:
        cases: list[TestCase] = []
        for rec in self._records(full):
            actions = rec.get("actions") or []
            if not actions:
                continue  # keep only tasks with annotated ground-truth tool calls
            seq = [a["name"] for a in actions]
            # tool_args keyed by tool name; first occurrence wins (the BFCL-style
            # param scorer matches the first call per tool). Each value is wrapped
            # in a single-element list so the scorer's "any acceptable value"
            # semantics degenerate to exact structural match — correct for τ-bench
            # where each arg has exactly one ground-truth value (incl. lists/dicts).
            tool_args: dict[str, dict] = {}
            for a in actions:
                tool_args.setdefault(a["name"], {k: [v] for k, v in (a.get("kwargs") or {}).items()})
            tid = f"{self.info.suite_id}-{rec['domain']}-{rec['task_index']}"
            cases.append(TestCase(
                test_id=tid, suite_id=self.info.suite_id, version=1,
                task_description=(rec.get("instruction") or "")[:200],
                input={"instruction": rec.get("instruction", ""),
                       "domain": rec["domain"], "user_id": rec.get("user_id", ""),
                       "annotator": rec.get("annotator", ""),
                       "tau_bench_id": tid},
                expected={"required_tools": list(dict.fromkeys(seq)),
                          "tool_sequence": seq, "tool_args": tool_args,
                          "abstain": False, "source": "tau-bench",
                          "domain": rec["domain"], "tau_bench_id": tid},
                tags=["multi_turn", "standard", rec["domain"]],
                rubric_id=f"{self.info.suite_id}-rubric"))
        return cases

    def rubric(self) -> Rubric:
        rid = f"{self.info.suite_id}-rubric"
        checks = [
            ("tool_selection_accuracy", "binary", "Correct tools selected (τ-bench)"),
            ("tool_param_accuracy", "three_point", "Correct arguments vs τ-bench ground truth"),
            ("tool_sequence_accuracy", "binary", "Correct ordered tool trajectory (τ-bench)"),
            ("abstention_correct", "binary", "Acts when the task warrants tool use (τ-bench)"),
        ]
        return Rubric(rubric_id=rid, version=1, criteria=[
            Criterion(criterion_id=ref, description=desc, scorer="code",
                      scale=scale, check_ref=ref, tags=["trajectory", "standard", "tau-bench"])
            for (ref, scale, desc) in checks])


def attribution_text() -> str:
    return (_DATA / "ATTRIBUTION.md").read_text()
