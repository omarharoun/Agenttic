"""Dataset ingestion adapters — pull a real public agent-eval dataset and map it
into an agenttic standard suite scored by our canonical checks.

BFCL is the first; tau-bench and AgentHarm follow via the same pattern: subclass
``DatasetAdapter``, parse the dataset's records into ``TestCase``s that preserve
its ground truth in ``expected`` (so an existing canonical check scores it), and
declare a ``Rubric`` of the relevant canonical check_refs. Licensing/attribution
travels with each adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ascore.registry.sqlite_store import NotFoundError
from ascore.schema.rubric import Rubric
from ascore.schema.testcase import TestCase, TestSuite


@dataclass(frozen=True)
class DatasetInfo:
    dataset_id: str       # e.g. "bfcl"
    suite_id: str         # e.g. "bfcl-simple-v3"
    name: str             # display name ("BFCL simple (real dataset)")
    citation: str
    license: str          # SPDX, e.g. "Apache-2.0"
    source_url: str
    #: True when the upstream dataset is access-gated (requires accepting terms /
    #: auth to fetch). Surfaced so the UI/methodology can show "gated — bring your
    #: own access" and so ``--full`` ingest documents the auth step. Defaults to
    #: False (public datasets); set True by gated adapters (e.g. GAIA).
    gated: bool = False
    #: True when official scoring of this dataset requires a heavy *execution*
    #: harness we do not run here (e.g. SWE-bench's Docker resolve-rate harness:
    #: apply the patch, run FAIL_TO_PASS / PASS_TO_PASS in the repo's container).
    #: Surfaced so the UI/methodology can show the honest caveat that the suite
    #: is scored by an OFFLINE PROXY, not the dataset's official execution metric.
    #: Defaults to False; set True by execution-gated adapters (e.g. SWE-bench).
    requires_execution_harness: bool = False


class DatasetAdapter(ABC):
    info: DatasetInfo

    @abstractmethod
    def load_records(self, *, full: bool = False) -> list[TestCase]:
        """Parse the dataset into canonical TestCases (vendored sample by
        default; the full set when ``full`` and network allows)."""

    @abstractmethod
    def rubric(self) -> Rubric:
        """The canonical-check rubric the suite is scored with."""

    def build_suite(self, cases: list[TestCase]) -> TestSuite:
        return TestSuite(
            suite_id=self.info.suite_id, version=1, approved=True,
            business_context=f"{self.info.name} — REAL public dataset "
            f"({self.info.license}). Source: {self.info.source_url}. {self.info.citation}",
            test_ids=[c.test_id for c in cases])

    def ingest(self, reg, *, full: bool = False) -> dict:
        """Install the dataset suite into ``reg`` (idempotent). Returns a summary."""
        try:
            reg.get_suite(self.info.suite_id)
            return {"suite_id": self.info.suite_id, "ingested": 0, "already_present": True}
        except NotFoundError:
            pass
        cases = self.load_records(full=full)
        reg.save_rubric(self.rubric())
        reg.save_suite(self.build_suite(cases), cases)
        return {"suite_id": self.info.suite_id, "ingested": len(cases),
                "already_present": False, "dataset": self.info.dataset_id,
                "license": self.info.license, "source": self.info.source_url}
