"""Coverage — what "tested" means, and what was never exercised (SPEC-13).

Closure over a declared coverage model, not pass rate, is the headline
(Hard Rule 56). Importing this package registers the deterministic extractors.
"""

from agenttic.coverage.collect import (  # noqa: F401
    CoverageReport, Sample, collect)
from agenttic.coverage.extractors import PREDICATES, predicate  # noqa: F401
from agenttic.coverage.model import (  # noqa: F401
    Bin, Classifier, CoverageModel, Coverpoint, Cross)

__all__ = ["Bin", "Classifier", "CoverageModel", "Coverpoint", "Cross",
           "CoverageReport", "Sample", "collect", "PREDICATES", "predicate"]
