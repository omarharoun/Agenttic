"""GAIA (General AI Assistants) ingestion adapter.

GAIA is the general AI-assistant benchmark: real-world questions that require
tool use and multi-step reasoning, each with a single short ground-truth answer
and a difficulty ``Level`` (1–3). We map GAIA **validation** records (question +
final answer + level) into agenttic TestCases scored by the GAIA-normalized
answer-accuracy check ``gaia_answer_match`` (a re-implementation of GAIA's own
``question_scorer`` normalized exact match; see metrics/canonical_checks.py).

ACCESS-GATED. Unlike BFCL/AgentDojo, GAIA (``gaia-benchmark/GAIA``) is gated on
HuggingFace: fetching requires accepting the dataset terms AND authenticating
with an HF token. The held-out TEST split has no public answers, so we ingest the
VALIDATION split (which does). Because it is gated, we do NOT vendor any real
GAIA content — the vendored sample is a tiny, clearly-labeled, NON-OPERATIONAL
placeholder that mirrors only the record schema (see gaia_data/ATTRIBUTION.md +
LICENSE.txt). ``full=True`` fetches the real validation split from HF at runtime
using ``HF_TOKEN`` / ``HUGGING_FACE_HUB_TOKEN`` (must have accepted the terms).
The dataset info is marked ``gated=True`` so the UI/methodology can show
"gated — bring your own access".
"""

from __future__ import annotations

import json
import os
import urllib.request
from importlib import resources

from agenttic.metrics.datasets.base import DatasetAdapter, DatasetInfo
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import TestCase

# GAIA validation metadata (one JSON object per line). Gated: needs an HF token
# whose account has accepted the dataset terms.
_HF = ("https://huggingface.co/datasets/gaia-benchmark/GAIA/resolve/main/"
       "2023/validation/metadata.jsonl")
_DATA = resources.files("agenttic.metrics.datasets") / "gaia_data"

INFO = DatasetInfo(
    dataset_id="gaia",
    suite_id="gaia-v1",
    name="GAIA validation (real dataset, gated)",
    citation="Mialon, Fourrier, Swift, Wolf, LeCun, Scialom, "
             "GAIA: A Benchmark for General AI Assistants (arXiv:2311.12983).",
    license="CC-BY-4.0 (gated — accept terms on HuggingFace)",
    source_url="https://huggingface.co/datasets/gaia-benchmark/GAIA",
    gated=True,
)


def _jsonl(text: str) -> list[dict]:
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def _hf_token() -> str | None:
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        tok = os.environ.get(var)
        if tok:
            return tok
    return None


def _fetch_validation() -> list[dict]:
    """Fetch the gated GAIA validation metadata from HuggingFace. Requires an HF
    token (env) whose account has accepted the GAIA terms; otherwise HF returns
    401 — the gating, surfaced honestly."""
    headers = {"User-Agent": "agenttic-ingest"}
    tok = _hf_token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(_HF, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return _jsonl(r.read().decode())


class GAIAAdapter(DatasetAdapter):
    """GAIA validation split — general AI-assistant questions scored by GAIA
    normalized exact-match answer accuracy. Gated; see gaia_data/."""

    info = INFO

    def _records(self, full: bool) -> list[dict]:
        if full:
            # Real gated validation split — fetched at runtime, never vendored.
            return _fetch_validation()
        # Vendored NON-OPERATIONAL placeholder sample (skip the _comment header).
        raw = _jsonl((_DATA / "gaia_validation.sample.jsonl").read_text())
        return [r for r in raw if "task_id" in r]

    def load_records(self, *, full: bool = False) -> list[TestCase]:
        cases: list[TestCase] = []
        for rec in self._records(full):
            tid = str(rec.get("task_id") or "")
            question = rec.get("Question") or rec.get("question") or ""
            # GAIA validation ships the answer under "Final answer".
            answer = rec.get("Final answer", rec.get("final_answer", ""))
            if not tid or not question or answer in (None, ""):
                continue  # only keep cases with a usable ground-truth answer
            level = rec.get("Level", rec.get("level"))
            has_file = bool(rec.get("file_name"))
            tags = ["happy_path", "standard", "gaia"]
            if level is not None:
                tags.append(f"level-{level}")
            cases.append(TestCase(
                test_id=f"{self.info.suite_id}-{tid}",
                suite_id=self.info.suite_id, version=1,
                task_description=str(question)[:200],
                input={"question": question, "level": level,
                       "file_name": rec.get("file_name", ""),
                       "has_file": has_file, "gaia_task_id": tid},
                expected={"final_answer": str(answer), "level": level,
                          "abstain": False, "source": "GAIA",
                          "gated": True, "gaia_task_id": tid},
                tags=tags,
                rubric_id=f"{self.info.suite_id}-rubric"))
        return cases

    def rubric(self) -> Rubric:
        rid = f"{self.info.suite_id}-rubric"
        return Rubric(rubric_id=rid, version=1, criteria=[
            Criterion(criterion_id="gaia_answer_match", scorer="code",
                      description="Final answer matches GAIA ground truth under "
                                  "GAIA normalized exact-match (answer accuracy)",
                      scale="binary", check_ref="gaia_answer_match",
                      # NOT tagged 'trajectory': it reads only the final output,
                      # so it also applies to black-box traces.
                      tags=["standard", "gaia", "answer_accuracy"])])


def attribution_text() -> str:
    return (_DATA / "ATTRIBUTION.md").read_text()
