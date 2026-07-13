"""Schema package — the platform's single source of truth (Step 1)."""
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace
from ascore.schema.testcase import TestCase, TestSuite
from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.scorecard import CriterionScore, RunScore, Scorecard
from ascore.schema.agent import DeclaredAgent

__all__ = ["SCHEMA_VERSION", "Span", "Trace", "TestCase", "TestSuite",
           "Criterion", "Rubric", "CriterionScore", "RunScore", "Scorecard",
           "DeclaredAgent"]
