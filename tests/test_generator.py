"""Step 8 acceptance tests (SPEC.md):
- Feeding a sample job description yields a reviewable draft suite of
  >=10 cases across >=2 tasks
- Unapproved suites refuse to run; approval unlocks them
"""

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from ascore.generator.pipeline import BenchmarkGenerator, GeneratorError
from ascore.harness.runner import SuiteNotApprovedError, run_suite
from ascore.registry.sqlite_store import Registry

JOB_DOC = """Support Operations Associate. Responsibilities: triage inbound
tickets to the right queue (billing, technical, general) and answer policy
questions using the company knowledge base. Refunds within 30 days."""


def reply(payload):
    return NS(content=[NS(type="text", text=json.dumps(payload))])


TASKS = {"tasks": [
    {"slug": "triage", "name": "Ticket triage",
     "description": "Route tickets to the correct queue."},
    {"slug": "policy_qa", "name": "Policy Q&A",
     "description": "Answer policy questions from the KB."},
]}

def criteria_payload(slug):
    return {"criteria": [
        {"criterion_id": f"{slug}_correct", "description": "Correct result",
         "scorer": "code", "scale": "binary",
         "check_ref": "final_output_matches_expected", "anchors": {}, "tags": []},
        {"criterion_id": f"{slug}_tone", "description": "Professional tone",
         "scorer": "judge", "scale": "three_point", "check_ref": None,
         "anchors": {"pass": "Calm, specific.", "fail": "Dismissive."}, "tags": []},
    ]}

def cases_payload(slug, n=5):
    return {"cases": [
        {"task_description": f"{slug} case {i}",
         "input": {"ticket": f"{slug} input {i}"},
         "expected": {"final_output": f"{slug} answer {i}"},
         "tags": [["happy_path", "edge_case", "adversarial"][i % 3]]}
        for i in range(n)
    ]}


class FakeGenClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.messages = NS(create=lambda **kw: self.replies.pop(0))


def make_generator(replies):
    return BenchmarkGenerator(model="gen-model", client=FakeGenClient(replies))


SCRIPT = [reply(TASKS),
          reply(criteria_payload("triage")), reply(cases_payload("triage")),
          reply(criteria_payload("policy_qa")), reply(cases_payload("policy_qa"))]


class TestGeneration:
    def test_draft_suite_ten_cases_two_tasks_review_file(self, tmp_path):
        reg = Registry(tmp_path / "db.sqlite")
        suite = make_generator(SCRIPT).generate_suite(
            JOB_DOC, suite_id="support-v1", registry=reg,
            review_dir=tmp_path / "review")
        stored, cases = reg.get_suite("support-v1")
        assert len(cases) >= 10
        assert len({c.rubric_id for c in cases}) >= 2          # >=2 tasks
        assert stored.approved is False
        review = (tmp_path / "review" / "support-v1.md").read_text()
        assert "DRAFT" in review and "Ticket triage" in review
        assert "Calm, specific." in review                      # anchors surfaced
        # rubrics persisted and valid
        assert reg.get_rubric("support-v1-triage").criteria[1].anchors["pass"]

    def test_markdown_fenced_json_tolerated(self, tmp_path):
        fenced = NS(content=[NS(type="text",
                                text="```json\n" + json.dumps(TASKS) + "\n```")])
        gen = make_generator([fenced])
        assert len(gen.extract_tasks(JOB_DOC)) == 2

    def test_invalid_json_twice_raises(self):
        bad = NS(content=[NS(type="text", text="not json")])
        with pytest.raises(GeneratorError, match="invalid JSON twice"):
            make_generator([bad, bad]).extract_tasks(JOB_DOC)

    def test_generator_cannot_emit_unanchored_judge_criteria(self):
        broken = {"criteria": [{"criterion_id": "x", "description": "d",
                                "scorer": "judge", "scale": "binary",
                                "check_ref": None, "anchors": {}, "tags": []}]}
        gen = make_generator([reply(broken)])
        with pytest.raises(Exception, match="Hard Rule 2"):
            gen.define_criteria(TASKS["tasks"][0], rubric_id="r")


class TestApprovalGateEndToEnd:
    class NullAdapter:
        agent_id, visibility = "null", "glass_box"
        def describe(self): return {}
        def config_hash(self): return "h"
        def run(self, i, *, test_case_id=None):  # pragma: no cover
            raise AssertionError("must not run before approval")

    def test_unapproved_refuses_then_approval_unlocks(self, tmp_path):
        reg = Registry(tmp_path / "db.sqlite")
        make_generator(SCRIPT).generate_suite(
            JOB_DOC, suite_id="support-v1", registry=reg,
            review_dir=tmp_path / "review")
        suite, cases = reg.get_suite("support-v1")
        with pytest.raises(SuiteNotApprovedError):
            asyncio.run(run_suite(self.NullAdapter(), suite, cases, reg))
        reg.approve_suite("support-v1", 1)
        suite, _ = reg.get_suite("support-v1")
        assert suite.approved is True
