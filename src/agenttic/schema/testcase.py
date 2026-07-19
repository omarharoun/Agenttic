"""Test case schema (UVM: sequences/tests in the test plan)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Tag = str  # conventional values: "happy_path", "edge_case", "adversarial"


class OracleSolution(BaseModel):
    """A reference solution that PROVES a case is solvable (SPEC-6 Step 25.1).

    Executed through the scoring engine's *code* checks during the oracle gate:
    a case whose own oracle fails its own code checks is a defect, not
    difficulty (Hard Rule 28). Judge criteria are exempt — they are not
    mechanically decidable from a reference output alone.
    """

    __test__ = False  # not a pytest class

    final_output: str
    #: reference tool names, in order, for trajectory-checked cases
    tool_sequence: list[str] | None = None
    #: for stateful cases (SPEC-7 29): reference tool CALLS ({name, args}) whose
    #: replay through the environment reaches goal_state; the oracle gate replays
    #: these to verify solvability against state, not just output
    tool_calls: list[dict] | None = None
    authored_by: Literal["human", "generated", "generated_human_verified"] = "generated"


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
    #: reference solution proving solvability (SPEC-6 25.1); None until authored
    oracle: OracleSolution | None = None
    #: per-case harness timeout override in seconds (SPEC-6 27, Harbor time limit)
    timeout_sec: float | None = None
    #: the stateful environment this case runs against (SPEC-7 Step 29); None for
    #: stateless (degenerate read-only) cases
    env_id: str | None = None


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
    #: import provenance, e.g. "harbor:swe-bench@2.0" (SPEC-6 Step 27); None for
    #: generated / hand-built suites
    origin: str | None = None
    #: a benchmark canary string preserved verbatim from an imported artifact
    #: (e.g. Terminal-Bench 2.0); NOT the per-tenant contamination canary (Step 28)
    canary: str | None = None
