"""Schema package — the platform's single source of truth (Step 1)."""
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.agent import DeclaredAgent

__all__ = ["SCHEMA_VERSION", "Span", "Trace", "TestCase", "TestSuite",
           "Criterion", "Rubric", "CriterionScore", "RunScore", "Scorecard",
           "DeclaredAgent"]
