"""SWE-bench Verified ingestion adapter — the gold-standard code-agent benchmark.

SWE-bench (Jimenez et al., ICLR 2024; arXiv:2310.06770) tasks an agent with a
real GitHub issue and the repository at the buggy ``base_commit``; the agent must
produce a code patch that **resolves** the issue. *Verified* is the 500-instance
human-validated subset (``princeton-nlp/SWE-bench_Verified``). Each record ships:
``repo``, ``base_commit``, ``problem_statement`` (the issue text), the **gold
patch** (``patch``), the test diff (``test_patch``), and the two test lists
``FAIL_TO_PASS`` / ``PASS_TO_PASS`` (JSON-encoded string lists).

WHAT "RESOLVED" REALLY MEANS — AND OUR HONEST CAVEAT
====================================================
SWE-bench's official metric is **resolve-rate**: a candidate patch is applied to
the repo at ``base_commit``, the project is built, and the tests are run inside a
per-instance **Docker** container — the instance is *resolved* iff every
``FAIL_TO_PASS`` test now passes AND every ``PASS_TO_PASS`` test still passes.
That execution harness is heavy infra (per-repo Docker images, builds, test
runs) which we do NOT have on the slim VM.

So this adapter is honest about scope. It maps the real records into TestCases
and scores them with an **offline PROXY**, never the official resolve-rate:

- ``swebench_patch_generated`` — did the agent emit a non-empty unified diff at
  all (a patch-rate signal; the prerequisite for any resolve).
- ``swebench_patch_targets_gold_files`` — fractional file-localization: of the
  files the *gold* patch edits, how many does the agent's patch also edit. This
  is a tractable static proxy for "did the agent touch the right code", **NOT**
  the official pass/fail of the hidden tests.

These are labeled "proxy, not official resolve-rate" everywhere they surface. The
``resolve_rate`` metric *interface* (see ``metrics.swebench_resolve``) documents
exactly what the real metric needs and raises if asked to score without the
Docker harness — wiring that harness is a flagged future infra task. We do NOT
report or claim any official SWE-bench numbers.

DATA / NETWORK
==============
SWE-bench is **MIT** licensed (see ``swebench_data/LICENSE`` + ``ATTRIBUTION.md``).
The dataset is public (not gated). A small **real** sample of the Verified test
split is vendored for offline ingest; ``full=True`` pulls the whole 500-instance
split from HuggingFace's datasets-server rows API where the network allows.
"""

from __future__ import annotations

import json
import re
import urllib.request
from importlib import resources

from agenttic.metrics.datasets.base import DatasetAdapter, DatasetInfo
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import TestCase

# HuggingFace datasets-server rows API (returns JSON rows; no parquet lib needed).
_HF_ROWS = ("https://datasets-server.huggingface.co/rows?dataset="
            "princeton-nlp%2FSWE-bench_Verified&config=default&split=test")
_DATA = resources.files("agenttic.metrics.datasets") / "swebench_data"
_SAMPLE = "swebench_verified.sample.jsonl"

INFO = DatasetInfo(
    dataset_id="swebench",
    suite_id="swebench-verified-v1",
    name="SWE-bench Verified (real dataset)",
    citation="Jimenez, Yang, Wettig, Yao, Pei, Press, Narasimhan, SWE-bench: Can "
             "Language Models Resolve Real-World GitHub Issues? (ICLR 2024; "
             "arXiv:2310.06770). Verified = the 500-instance human-validated subset.",
    license="MIT",
    source_url="https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified",
    # Public dataset, but official resolve-rate needs the Docker execution
    # harness we do not run here — surfaced so the UI shows the proxy caveat.
    requires_execution_harness=True,
)

# Matches the file path on the "+++ b/<path>" line (and the "diff --git a/.. b/.."
# header) of a unified diff. We localize on the post-image (b/) path.
_DIFF_GIT = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)\s*$", re.MULTILINE)
_PLUS_FILE = re.compile(r"^\+\+\+ b/(?P<path>.+?)\s*$", re.MULTILINE)


def patched_files(diff_text: str) -> set[str]:
    """Extract the set of repository file paths a unified diff modifies. Reads
    ``diff --git a/X b/Y`` headers (falling back to ``+++ b/Y`` lines), so it
    works on git-style patches whether or not the agent included file headers.
    Returns an empty set for an empty / non-diff string."""
    if not diff_text:
        return set()
    files = {m.group("b") for m in _DIFF_GIT.finditer(diff_text)}
    files |= {m.group("path") for m in _PLUS_FILE.finditer(diff_text)
              if m.group("path") != "/dev/null"}
    return files


def _jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _as_list(val) -> list[str]:
    """FAIL_TO_PASS / PASS_TO_PASS ship as JSON-encoded string lists upstream;
    tolerate both the encoded string and an already-decoded list."""
    if isinstance(val, list):
        return [str(x) for x in val]
    if isinstance(val, str) and val.strip():
        try:
            return [str(x) for x in json.loads(val)]
        except json.JSONDecodeError:
            return [val]
    return []


def _fetch_full() -> list[dict]:
    """Pull the entire Verified test split via the HF datasets-server rows API
    (paginated, 100 rows/request). Public — no token required."""
    out: list[dict] = []
    offset, page = 0, 100
    while True:
        url = f"{_HF_ROWS}&offset={offset}&length={page}"
        req = urllib.request.Request(url, headers={"User-Agent": "agenttic-ingest"})
        with urllib.request.urlopen(req, timeout=60) as r:
            rows = json.loads(r.read().decode()).get("rows", [])
        if not rows:
            break
        out.extend(x["row"] for x in rows)
        if len(rows) < page:
            break
        offset += page
    return out


class SWEBenchAdapter(DatasetAdapter):
    """SWE-bench Verified — real GitHub-issue resolution tasks. Scored OFFLINE by
    a documented file-localization PROXY, NOT the official Docker resolve-rate
    (which requires the execution harness; see swebench_data/ATTRIBUTION.md)."""

    info = INFO

    def _records(self, full: bool) -> list[dict]:
        if full:
            return _fetch_full()
        # Vendored REAL sample (skip the leading _comment header object).
        raw = _jsonl((_DATA / _SAMPLE).read_text())
        return [r for r in raw if r.get("instance_id")]

    def load_records(self, *, full: bool = False) -> list[TestCase]:
        cases: list[TestCase] = []
        for rec in self._records(full):
            iid = str(rec.get("instance_id") or "")
            repo = rec.get("repo") or ""
            problem = rec.get("problem_statement") or ""
            gold_patch = rec.get("patch") or ""
            if not iid or not problem or not gold_patch:
                continue  # need an issue and a gold patch to score even the proxy
            f2p = _as_list(rec.get("FAIL_TO_PASS"))
            p2p = _as_list(rec.get("PASS_TO_PASS"))
            gold_files = sorted(patched_files(gold_patch))
            tags = ["code_agent", "standard", "swebench"]
            diff = (rec.get("difficulty") or "").strip()
            if diff:
                tags.append(diff.lower().replace(" ", "-"))
            cases.append(TestCase(
                test_id=f"{self.info.suite_id}-{iid}",
                suite_id=self.info.suite_id, version=1,
                task_description=f"[{repo}] {problem}"[:200],
                input={"repo": repo, "instance_id": iid,
                       "base_commit": rec.get("base_commit", ""),
                       "environment_setup_commit": rec.get("environment_setup_commit", ""),
                       "version": rec.get("version", ""),
                       "problem_statement": problem},
                expected={
                    # Ground truth for the offline localization PROXY.
                    "gold_files": gold_files,
                    "patch_required": True,
                    # Carried so a future Docker harness can run the REAL metric.
                    "fail_to_pass": f2p, "pass_to_pass": p2p,
                    "base_commit": rec.get("base_commit", ""),
                    "repo": repo, "instance_id": iid,
                    "source": "SWE-bench Verified",
                    # Honesty flags echoed onto every case.
                    "requires_execution_harness": True,
                    "scoring": "offline_proxy_not_official_resolve_rate"},
                tags=tags,
                rubric_id=f"{self.info.suite_id}-rubric"))
        return cases

    def rubric(self) -> Rubric:
        rid = f"{self.info.suite_id}-rubric"
        checks = [
            ("swebench_patch_generated", "binary",
             "Agent produced a non-empty code patch (PROXY patch-rate; "
             "prerequisite for resolve — NOT official resolve-rate)"),
            ("swebench_patch_targets_gold_files", "three_point",
             "Fraction of gold-patch files the agent's patch also edits "
             "(PROXY file-localization — NOT the official Docker resolve-rate)"),
        ]
        return Rubric(rubric_id=rid, version=1, criteria=[
            Criterion(criterion_id=ref, description=desc, scorer="code",
                      scale=scale, check_ref=ref,
                      tags=["code_agent", "standard", "swebench", "proxy"])
            for (ref, scale, desc) in checks])


def attribution_text() -> str:
    return (_DATA / "ATTRIBUTION.md").read_text()
