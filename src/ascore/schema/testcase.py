"""Test case schema (UVM: sequences/tests in the test plan)."""

from __future__ import annotations

from pydantic import BaseModel, Field

Tag = str  # conventional values: "happy_path", "edge_case", "adversarial"


class TestCase(BaseModel):
    """A single benchmark scenario an agent must handle."""

    __test__ = False  # not a pytest class

    test_id: str
    suite_id: str
    version: int = 1
    task_description: str
    input: dict = Field(default_factory=dict)
    expected: dict | None = None  # ground truth, when deterministically checkable
    tags: list[Tag] = Field(default_factory=list)
    rubric_id: str


class TestSuite(BaseModel):
    """A versioned collection of test cases for one business context."""

    __test__ = False  # not a pytest class

    suite_id: str
    version: int = 1
    business_context: str
    test_ids: list[str] = Field(default_factory=list)
    approved: bool = False  # human gate (Step 8): unapproved suites refuse to run
    #: Dataset ingest provenance (Hard Rule 9): "real" when ingested from the FULL
    #: public split, "seed" when ingested from a vendored .sample split, None for
    #: non-dataset suites (std seeds, hand-built). Coverage resolves a domain to
    #: assessed_real ONLY for a "real" dataset suite — never for a sample/seed one.
    dataset_provenance: str | None = None
